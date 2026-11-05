// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module RoiAlignQ4 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    input logic [63:0] in_rois,
    output logic [255:0] out_values
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

logic [255:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for(integer r=0;r<2;r=r+1) begin
    integer x1,y1,w,h;
    x1=s8(in_rois[32*r +: 8]); y1=s8(in_rois[32*r+8 +: 8]);
    w=s8(in_rois[32*r+16 +: 8])-x1; if(w<16) w=16;
    h=s8(in_rois[32*r+24 +: 8])-y1; if(h<16) h=16;
    for(integer c=0;c<2;c=c+1)
      for(integer by=0;by<2;by=by+1)
        for(integer bx=0;bx<2;bx=bx+1) begin
          value=0;
          for(integer sy=0;sy<2;sy=sy+1)
            for(integer sx=0;sx<2;sx=sx+1) begin
              integer px,py,ix,iy,fx,fy;
              px=8*x1+(4*bx+2*sx+1)*w; py=8*y1+(4*by+2*sy+1)*h;
              ix=0; iy=0; fx=0; fy=0;
              if(px>=-128 && px<=512 && py>=-128 && py<=512) begin
                px=px<0 ? 0:px>384 ? 384:px; py=py<0 ? 0:py>384 ? 384:py;
                ix=px/128; iy=py/128; fx=px-128*ix; fy=py-128*iy;
                for(integer dy=0;dy<2;dy=dy+1)
                  for(integer dx=0;dx<2;dx=dx+1) begin
                    integer x,y,wx,wy;
                    x=ix+dx; if(x>3) x=3; y=iy+dy; if(y>3) y=3;
                    wx=dx ? fx:128-fx; wy=dy ? fy:128-fy;
                    value=value+wx*wy*s8(in_data[8*(16*c+4*y+x) +: 8]);
                  end
              end
            end
          value=away(value,256); result[16*(8*r+4*c+2*by+bx) +: 16]=value[15:0];
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
