/ prints every grouped aggregate for several key/value types and ranges (pipe through shasum)
\l amber.k
\l std.k
\l qsql.k
DIAG:`diag 0
n:50000;i:!n
pd:{`0:" "/$!x;`0:" "/$. x}
K:(10!i;`i$1000!7919*i;100000!2654435761*i;1000003*100!i;`$'$'7!i;-50+100!i;-7*`i$30000!i;1000003*`i$10000!7919*i)
fv:@[@[0.001*-500+1000!7919*i;&5=3001!i;:;0n];&7=997!i;:;-0.0]
lv:@[-500+1000!104729*i;&11=2011!i;:;0N]
iv:`i$-500+1000!104729*i
V:(fv;lv;iv;0.5*i)
fs:(gsum;gavg;gmin;gmax;gfirst;glast)
{[k]pd gcount k;{[k;v]{[k;v;f]pd f[k;v]}[k;v]'fs}[k]'V}'K;
t:([]sym:`$'$'100!i;px:fv;sz:lv;g:10!i)
pt:{{`0:" "/$x}'(. +!x),. +. x}
pt sel"select s:sum px, a:avg px, mn:min px, mx:max px, c:count px, f:first px, l:last px by sym from t where sz>0"
pt sel"select s:sum sz, a:avg sz, mn:min sz, mx:max sz, c:count sz, f:first sz, l:last sz by g from t where px>0.1"
pt sel"select s:sum px by sym from t"
pt sel"select v:wavg[sz;px] by g from t where sz>-100"
