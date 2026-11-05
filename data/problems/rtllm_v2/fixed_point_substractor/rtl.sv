module TopModule #(parameter int Q = 15, parameter int N = 32)
 (input [N-1:0] a, b, output [N-1:0] c);
 reg sign;
 reg [N-2:0] magnitude;
 always @* begin
   if (a[N-1] != b[N-1]) begin
     magnitude = a[N-2:0] + b[N-2:0]; sign = a[N-1];
   end else if (a[N-2:0] >= b[N-2:0]) begin
     magnitude = a[N-2:0] - b[N-2:0]; sign = a[N-1];
   end else begin
     magnitude = b[N-2:0] - a[N-2:0]; sign = ~a[N-1];
   end
   if (magnitude == 0) sign = 0;
 end
 assign c = {sign,magnitude};
endmodule
