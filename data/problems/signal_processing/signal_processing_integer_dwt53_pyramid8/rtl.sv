// SPDX-License-Identifier: BSD-2-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module DWT53Pyramid8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [127:0] in_data,
    output logic [255:0] out_values
);
function automatic signed [31:0] s16(input logic [15:0] x);
  s16 = {{16{x[15]}}, x};
endfunction

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : wavelet_pyramid
    integer signed data[0:7], detail[0:3];
    integer half, left_index, right_index;
    half=0; left_index=0; right_index=0;
    for(integer i=0;i<8;i=i+1) data[i]=s16(in_data[16*i +: 16]);
    for(integer i=0;i<4;i=i+1) detail[i]=0;
    for(integer level=0;level<3;level=level+1) begin
      half=4 >> level;
      for(integer i=0;i<4;i=i+1) if(i<half) begin
        right_index=(i+1<half)?i+1:i;
        detail[i]=data[2*i+1]-((data[2*i]+data[2*right_index]) >>> 1);
      end
      for(integer i=0;i<4;i=i+1) if(i<half) begin
        left_index=(i>0)?i-1:0;
        data[i]=data[2*i]+((detail[left_index]+detail[i]+2) >>> 2);
      end
      for(integer i=0;i<4;i=i+1) if(i<half) data[half+i]=detail[i];
    end
    for(integer i=0;i<8;i=i+1) result[32*i +: 32]=data[i];
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
