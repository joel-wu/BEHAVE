// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module GridSampleQ4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [143:0] in_data,
    input logic [63:0] in_grid,
    input logic [0:0] in_align_corners,
    input logic [0:0] in_border,
    output logic [127:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
function automatic signed [31:0] away(input logic signed [63:0] n, input integer d);
  logic signed [63:0] q;
  begin
    q=n<0 ? -((-n+d/2)/d) : (n+d/2)/d;
    away=q[31:0];
  end
endfunction

logic [127:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer c=0;c<2;c=c+1)
    for(integer k=0;k<4;k=k+1) begin
      integer gx,gy,px,py,ix,iy,fx,fy;
      gx=s8(in_grid[16*k +: 8]); gy=s8(in_grid[16*k+8 +: 8]);
      px=in_align_corners ? 2*gx+32 : 3*gx+32;
      py=in_align_corners ? 2*gy+32 : 3*gy+32;
      ix=px >>> 5; iy=py >>> 5; fx=px-32*ix; fy=py-32*iy;
      value=0;
      for(integer dy=0;dy<2;dy=dy+1)
        for(integer dx=0;dx<2;dx=dx+1) begin
          integer x,y,wx,wy;
          x=ix+dx; y=iy+dy; wx=dx ? fx:32-fx; wy=dy ? fy:32-fy;
          if(in_border) begin
            x=x<0 ? 0:x>2 ? 2:x; y=y<0 ? 0:y>2 ? 2:y;
          end
          if(x>=0 && x<3 && y>=0 && y<3)
            value=value+wx*wy*s8(in_data[8*(9*c+3*y+x) +: 8]);
        end
      value=away(value,4); result[16*(4*c+k) +: 16]=value[15:0];
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
