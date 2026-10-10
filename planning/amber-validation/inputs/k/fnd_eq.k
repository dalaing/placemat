/ prints find, in and distinct for several types, lengths and probe counts (pipe through shasum)
\l amber.k
n:200000;i:!n
H:{x@!#x}'(1048573!262147*i;`i$1000!7919*i;-5+100000!2654435761*i;@[3*i;&0=7!i;:;0N];`$'$'1000!7919*i)
P:{[h](h@(#h)!97*!5000),(h@!3),(h@(#h)-1+!3)}'H
P:P,'(5000#-1;5000#`i$-1;-99 -98;0N 1 2;`zz`k1)      / misses mixed in
pr:{x:x@!#x;`0:" "/$x}
{[h;p]pr[h?p];pr[p in h];pr[in[p;h]];pr[?h];pr[h?100#p]}'[H;P];
pr (1048573!262147*i)?1048573!262147*!5000
pr (5000#1)?1 2 3
pr (!0)?1 2
