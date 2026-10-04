/ scout 5M grouped sums (hot/k/grp.k's sc_group_* cases, one out[] per line so the stage finds them)
\l amber.k
\l std.k
DIAG:`diag 0
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
NN:5000000;HS:1048573!262147*!NN;X:0.0+1000!HS
G10:10!HS;G10K:10000!HS;G100K:100000!HS
out["sc_group_10";3;{gsum[G10;X]}]
out["sc_group_10k";3;{gsum[G10K;X]}]
out["sc_group_100k";3;{gsum[G100K;X]}]
