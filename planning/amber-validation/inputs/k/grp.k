/ grouped aggregation: suite.k 1M (gsum), scout 5M (gsum, + qsql-shaped masked), qbench 1M (select ... by)
\l amber.k
\l std.k
\l qsql.k
DIAG:`diag 0
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
N:1000000;H:100000!2654435761*!N;PX:0.001*H;G10:10!H;G1K:1000!H
out["s_grp10";10;{gsum[G10;PX]}]
out["s_grp1k";10;{gsum[G1K;PX]}]
out["s_grp100k";10;{gsum[H;PX]}]
NN:5000000;HS:1048573!262147*!NN;X:0.0+1000!HS
G10:10!HS;G10K:10000!HS;G100K:100000!HS
out["sc_group_10";3;{gsum[G10;X]}]
out["sc_group_10k";3;{gsum[G10K;X]}]
out["sc_group_100k";3;{gsum[G100K;X]}]
G:0
HT:1048573!262147*!2000000;SY:100!HT;PXT:0.0+1000!HT;SZ:500!HT
out["sc_qsql_select";5;{m:&SZ>250;gsum[SY@m;PXT@m]}]
i:!N;S:`$'$'!100;t:([]sym:S 100!i;px:100.0+0.01*1000!i;sz:1+500!i)
out["q_groupby_sum";5;{sel"select sum px by sym from t"}]
out["q_filter_groupby";5;{sel"select sum sz by sym from t where px>105"}]
