/ reductions: final.k (20M and 100k longs/ints), suite/scout float sum and max
rep:{[k;f]t:`t[];k{[f;x]f[];x}[f]/0;`t[]-t}
out:{[nm;k;f]`0:nm," ",$rep[k;f]}
S:20000000;v:3000000000+S#!1000;w:S#!100
out["f_prod_long20m";5;{*/v}]
out["f_min_long20m";5;{&/v}]
out["f_max_long20m";5;{|/v}]
out["f_min_int20m";5;{&/w}]
out["f_sum_long20m";5;{+/v}]
v:0;w:0
v2:3000000000+100000#!1000;w2:100000#!100
out["f_prod_long100k";400;{*/v2}]
out["f_min_long100k";400;{&/v2}]
out["f_max_long100k";400;{|/v2}]
out["f_sum_long100k";400;{+/v2}]
out["f_sum_int100k";400;{+/w2}]
PX:0.001*100000!2654435761*!1000000
out["s_sum1m";100;{+/PX}]
X:0.0+1000!1048573!262147*!5000000
out["sc_sum_f5m";20;{+/X}]
out["sc_max_f5m";20;{|/X}]
