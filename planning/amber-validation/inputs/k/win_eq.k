/ prints every moving-window result for several inputs and widths (pipe through shasum)
\l amber.k
\l std.k
n:70000;i:!n
d1:0.001*100000!2654435761*i
d2:@[@[@[0.001*-500+1000!7919*i;&5=30011!i;:;0n];&7=997!i;:;-0.0];&39000=40009!i;:;0w]
d3:@[-500+1000!7919*i;&11=20011!i;:;0N]
d4:`i$-500+1000!104729*i
d5:sin 0.0+i
D:(d1;d2;d3;d4;d5)
W:1 2 3 16 63 64 65 100 1000 4095 4096 4097
fs:(msum;mavg;mvar;mdev;mmin;mmax)
{[d]{[d;w]{[d;w;f]`0:" "/$f[w;d]}[d;w]'fs}[d]'W}'D;
