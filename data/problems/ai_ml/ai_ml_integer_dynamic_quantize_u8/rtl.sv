// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module DynamicQuantizeU8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [87:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction
function automatic signed [31:0] round_even_ratio(input logic signed [63:0] n, input logic [31:0] d);
  logic [63:0] a,q,rem;
  begin
    a=n<0 ? -n : n; q=a/d; rem=a%d;
    if(2*rem>d || (2*rem==d && q[0])) q=q+1;
    round_even_ratio=n<0 ? -$signed(q[31:0]) : $signed(q[31:0]);
  end
endfunction
logic [87:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : quantizer
    integer low,high,denominator,zero;
    low=0; high=0;
    for(integer i=0;i<8;i=i+1) begin
      value=s16(in_data[16*i +: 16]);
      if(value<low) low=value;
      if(value>high) high=value;
    end
    denominator=high-low;
    if(denominator==0) denominator=1;
    zero=round_even_ratio(-64'sd255*64'(low),32'(denominator));
    result[71:64]=8'(zero); result[87:72]=16'(denominator);
    for(integer i=0;i<8;i=i+1) begin
      value=round_even_ratio(64'sd255*64'(s16(in_data[16*i +: 16])),32'(denominator))+zero;
      if(value<0) value=0;
      if(value>255) value=255;
      result[8*i +: 8]=8'(value);
    end
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;
  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;
  end
end
endmodule
