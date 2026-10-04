/ find / in / distinct: bench.k shapes (5000 probes into 100k..5M, `in 5000 into 2M), scout find/member/distinct, suite distinct
\l amber.k
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
mk:{[n]base:asc n?100*n;plain:0+base;probe:base@5000?#base;(base;plain;probe)}
d:mk 100000;B1:d 0;L1:d 1;P1:d 2
d:mk 2000000;B2:d 0;L2:d 1;P2:d 2
d:mk 5000000;B5:d 0;L5:d 1;P5:d 2
out["b_find_lin_100k";20;{L1?P1}]
out["b_find_lin_2m";3;{L2?P2}]
out["b_find_lin_5m";2;{L5?P5}]
out["b_find_bin_5m";20;{B5?P5}]
d:0;B1:0;L1:0;P1:0;B2:0;L2:0;P2:0;B5:0;L5:0;P5:0
b:asc 2000000?100000000;pp:b@5000?#b;lb:0+b
out["b_in_lin";3;{in[pp;lb]}]
out["b_in_bin";3;{in[pp;b]}]
b:0;lb:0
HS:1048573!262147*!5000000;A:1000!HS;KR:1048573!7919*!1000;PR:KR@1000!HS
out["sc_find";5;{+/KR?PR}]
out["sc_member";5;{+/HS in KR}]
out["sc_distinct";5;{?A}]
G5:100000!HS
out["sc_distinct_100k";5;{?G5}]
H:100000!2654435761*!1000000
out["s_distinct";20;{?H}]
