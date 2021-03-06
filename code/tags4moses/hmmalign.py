#!/usr/bin/env python

import sys
import gzip, io
from collections import defaultdict
from itertools import imap

from moparser import MosesOutputParser

class Vocabulary(object):
    def __init__(self, f):
        self.voc, self.inv_voc = self.read_vocab(f)

    def read_vocab(self, f):
        voc = {}
        inv_voc = {}
        for idx, word, count in imap(str.split, f):
            idx = int(idx)
            voc[word] = idx
            inv_voc[idx] = word
        return voc, inv_voc

    def map_sentence(self, snt, lowercase=False):
        snt = snt.strip().split()
        if lowercase:
            snt = snt.lower()
        return [self.voc.get(w,0) for w in snt]

class LexTrans(object):
    def __init__(self, f, min_p=0.0):
        self.lex_probs = defaultdict(dict)
        for line in imap(str.split, f):
            if len(line) == 3:
                src, tgt, p = line
            else:
                assert len(line) == 4
                src, tgt, cnt, p = line
            if float(p) > min_p:
                self.lex_probs[int(src)][int(tgt)] = float(p)   # p(tgt|src)

    def sum_transtable(self):
        for src in self.lex_probs:
            print src, sum(self.lex_probs[src].values())

    def __contains__(self, src):
        return src in self.lex_probs

    def get(self, src, tgt, default=0.0):
        assert src in self.lex_probs, "no probs for src word %s\n" %(src)
        return self.lex_probs[src].get(tgt, 0.0)

class HMMAligner(object):
    def __init__(self, hmm_file, lex_file):
        self.lex_probs = LexTrans(lex_file)
        self.transition_probs = defaultdict(dict)
        if hmm_file is not None:
            self.read_hmm(hmm_file)

    def read_hmm(self, f):
        # read a file that look like this
        # 3 -2:182.773; -1:1106.93; 0:664.036; 1:44.329; 2:26.9507;
        for linenr, line in enumerate(f):
            line = line.rstrip().replace(';','').split()
            tgt_len = int(line[0])
            for jump, s in imap(lambda x:x.split(':'), line[1:]):
                self.transition_probs[tgt_len][int(jump)] = float(s)

        for tgt_len, probs in self.transition_probs.iteritems():
            s_sum = sum(probs.values())
            for jump, s in probs.iteritems():
                probs[jump] /= s_sum

    def align(self, src, tgt, pnull=.4, phrase_alignment=None):
        Q = self.viterbi( src, tgt, pnull, phrase_alignment)
        a = self.viterbi_alignment(Q)
        a.reverse()
        return a

    def init_q(self, J, I, alignment):
        Q = [[None]*I*2 for s in range(J)]
        for src_idx, tgt_idx in alignment:
            assert len(tgt_idx)>0
            for j in tgt_idx:
                if len(src_idx) == 0: # unaligned
                    for i in range(I):
                        Q[j][i] = (0.,-1)
                        Q[j][i+I] = (1.,-1)
                else:
                    for i in range(I):
                        Q[j][i] = (0.,-1) # mark all words impossible
                    for i in src_idx:
                        Q[j][i] = None      # mark aligned words possible
        return Q

    def get_jumpprob(self, I, jump):
        """ return probability of jump

            assuming a jump distribution based on sentence length
            if no data is available all jumps have equal probability (1.0)
        """
        if not I in self.transition_probs:
            return 1.0
        return self.transition_probs[I].get(jump, 0.0)


    def viterbi(self, src, tgt, pnull, phrase_alignment):
        I = len(src)
        J = len(tgt)
        Q = [[None]*I*2 for s in tgt]
        if phrase_alignment:
            Q = self.init_q(J, I, phrase_alignment)
        #jump_probs = self.transition_probs[I]
        for j in range(J):
            w_t = tgt[j]
            for i in range(2*I):  # a_j
                if not Q[j][i] == None:
                    continue
                w_s = 0
                if i < I:
                    w_s = src[i]
                assert w_s in self.lex_probs
                lex_prob = self.lex_probs.get(w_s, w_t, default=0.0)
                if j == 0: # first word
                    jump_prob = 1.0
                    Q[j][i] = (jump_prob * lex_prob, -1)
                else:
                    best = None
                    q_max = 1.0
                    try:
                        q_max = max(q[0] for q in Q[j-1] if not q==None)
                    except ValueError:
                        pass
                    for k in range(2*I): # a_{j-1}
                        jump_prob = 0.0
                        if i < I:
                            jump = i - (k%I)
                            jump_prob = self.get_jumpprob(I, -jump)
                            #print 'jump, jumpprob', jump, jump_prob
                        else: # align to nullword
                            if k%I == i:
                                jump_prob = pnull
                        prev_prob = Q[j-1][k][0]
                        if q_max > 0:
                            prev_prob /= q_max
                        prob = jump_prob * prev_prob
                        if best == None or best[1] < prob:
                            best = (k, prob)
                    Q[j][i] = (best[1]*lex_prob, best[0])
        # self.__printQ(Q, transpose=True)
        return Q

    def __printQ(self, Q, transpose=False):
        """ mostly for debugging """
        if transpose:
            for j in range(len(Q)):
                for i in range(len(Q[0])):
                    print "Q(%s,%s)=%s" %(j,i,str(Q[j][i]))
        else:
            for i in range(len(Q[0])):
                for j in range(len(Q)):
                    print "Q(%s,%s)=%s" %(j,i,str(Q[j][i]))

    def viterbi_alignment(self, Q, verbose=False): # backtrace
        j = len(Q)-1
        alignment = []
        best = None
        best_idx = None
        for i in range(len(Q[j])):
            if best == None or Q[j][i][0] > best[0]:
                best = Q[j][i];
                best_idx = i
        while j>=0:
            if verbose:
                print j+1, best_idx+1, "->", Q[j][best_idx][1]
            a_j = best_idx
            if best_idx >= len(Q[j])/2:
                a_j = -1
            alignment.append((j, a_j))

            best_idx = Q[j][best_idx][1]
            j -= 1
        return alignment

def smart_open(filename):
    if not filename:
        return None
    if filename.endswith('.gz'):
        return io.BufferedReader(gzip.open(filename))
    return open(filename)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('hmmfile', action='store', help="HMM transition probs from GIZA++")
    parser.add_argument('lexprobs', action='store', help="translation probs")
    parser.add_argument('sourcevoc', action='store', help="source vocabulary")
    parser.add_argument('targetvoc', action='store', help="target vocabulary")
    parser.add_argument('-pnull', action='store', type=float, help="jump probability to/from NULL word (default: 0.4)", default=0.4)
    parser.add_argument('-lower', action='store_true', help='lowercase input')
    parser.add_argument('-verbose', action='store_true', help='more output')
    parser.add_argument('-ignore_phrases', action='store_true', help='ignore alignment info from moses')
    args = parser.parse_args(sys.argv[1:])

    hmm = HMMAligner(smart_open(args.hmmfile), smart_open(args.lexprobs))
    src_voc = Vocabulary(smart_open(args.sourcevoc))
    tgt_voc = Vocabulary(smart_open(args.targetvoc))

    parser = MosesOutputParser()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            print line
            continue
        src_txt, tgt_txt, align, tag, markup = parser.parse(line)

        if args.verbose:
            sys.stderr.write("src: %s\ntgt: %s\nalign: %s\n" %(src_txt, tgt_txt, str(align)))

        src = src_voc.map_sentence(src_txt, args.lower)
        tgt = tgt_voc.map_sentence(tgt_txt, args.lower)

        if args.verbose:
            sys.stderr.write("src: %s\ntgt: %s\n" %(str(src), str(tgt)))

        # compute a target-to-source alignment:
        # each target word is aligned to none or one source words
        if args.ignore_phrases:
            align = None
        alignment = hmm.align(src, tgt, phrase_alignment=align)
        alignment = dict(alignment)

        if args.verbose:
            sys.stderr.write("alignment: %s\n" %(str(alignment)))

        sys.stdout.write(markup)
        for j, w in enumerate(tgt_txt.rstrip().split()):
            if j>0:
                sys.stdout.write(" ")
            sys.stdout.write("%s |%s|" %(w, alignment[j]))
        sys.stdout.write("\n")

    sys.exit()



    tgt = "4908 2053 4443 72".split()     # Musharafs letzter Akt ?
    src = "1580 12 5651 3533 75".split()  # Musharf 's last Act ?

    src = map(int,"3308 6 767 2946 103 3 6552 1580 28 8938 468 12 1260 1294 7 1652 9 122 5 2183 4".split())
    tgt = map(int,"7 30 10421 722 2 37 5 148 7020 2 38 7690 1943 9 638 5 2739 491 1085 6 9 10288 12029 4".split())

    src = "desperate to hold onto power , Pervez Musharraf has discarded Pakistan &apos;s constitutional framework and declared a state of emergency ."
    tgt = "in dem verzweifelten Versuch , an der Macht festzuhalten , hat Pervez Musharraf den Rahmen der pakistanischen Verfassung verlassen und den Notstand ausgerufen ."


    print src
    print tgt
