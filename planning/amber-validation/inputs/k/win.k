/ moving windows: the maintainer's cases (suite.k 1M, bench-std.k 1M w=20, scout 5M). Prints "name us".
\l amber.k
\l std.k
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
N:1000000;H:100000!2654435761*!N;PX:0.001*H
dat:0.0+N?100
out["s_msum100";10;{msum[100;PX]}]
out["s_mavg100";10;{mavg[100;PX]}]
out["s_mdev100";10;{mdev[100;PX]}]
out["s_mmin100";10;{mmin[100;PX]}]
out["s_mmax100";10;{mmax[100;PX]}]
out["s_mavg10";10;{mavg[10;PX]}]
out["s_mavg1000";10;{mavg[1000;PX]}]
out["s_mmin10";10;{mmin[10;PX]}]
out["s_mmin1000";10;{mmin[1000;PX]}]
out["std_msum20";10;{msum[20;dat]}]
out["std_mavg20";10;{mavg[20;dat]}]
out["std_mdev20";10;{mdev[20;dat]}]
out["std_mmin20";10;{mmin[20;dat]}]
X:0.0+1000!1048573!262147*!5000000
out["sc_msum16";4;{+/msum[16;X]}]
out["sc_mavg256";4;{+/mavg[256;X]}]
out["sc_mmax64";4;{+/mmax[64;X]}]
