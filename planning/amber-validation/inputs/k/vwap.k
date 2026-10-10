\l amber.k
\l std.k
\l qsql.k
DIAG:`diag 0
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
N:1000000;i:!N;S:`$'$'!100;t:([]sym:S 100!i;px:100.0+0.01*1000!i;sz:1+500!i)
a:sel"select vwap:wavg[sz;px] by sym from t";b:sel"select vwap:sz wavg px by sym from t"
`0:"same ",$a~b
`0:"maxdiff ",$|/abs(. +. a)[0]-(. +. b)[0]
out["vwap_bracket";5;{sel"select vwap:wavg[sz;px] by sym from t"}]
out["vwap_infix";5;{sel"select vwap:sz wavg px by sym from t"}]
