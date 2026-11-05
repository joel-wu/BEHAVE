// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module QOIRGB8Payload (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [191:0] in_pixels,
    output logic [261:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction

logic [261:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : qoi_payload
    logic [31:0] cache[0:63]; logic [31:0] previous,pixel;
    logic [255:0] payload;
    integer run_v,count_v,r,g,b,slot,dr,dg,db,dgr,dgb;
    for(integer i=0;i<64;i=i+1) cache[i]=0;
    previous=32'hff000000; pixel=0; payload=0; run_v=0; count_v=0;
    r=0; g=0; b=0; slot=0; dr=0; dg=0; db=0; dgr=0; dgb=0;
    for(integer i=0;i<8;i=i+1) begin
      pixel={8'hff,in_pixels[24*i +: 24]};
      if(pixel==previous) begin
        run_v=run_v+1;
        if(i==7) begin payload[8*count_v +: 8]=192 | (run_v-1); count_v=count_v+1; run_v=0; end
      end else begin
        if(run_v>0) begin payload[8*count_v +: 8]=192 | (run_v-1); count_v=count_v+1; run_v=0; end
        r=pixel[7:0]; g=pixel[15:8]; b=pixel[23:16]; slot=(3*r+5*g+7*b+2805)&63;
        if(cache[slot]==pixel) begin payload[8*count_v +: 8]=slot; count_v=count_v+1; end
        else begin
          cache[slot]=pixel;
          dr=s8(r-previous[7:0]); dg=s8(g-previous[15:8]); db=s8(b-previous[23:16]);
          dgr=s8(dr-dg); dgb=s8(db-dg);
          if(dr>=-2 && dr<=1 && dg>=-2 && dg<=1 && db>=-2 && db<=1) begin
            payload[8*count_v +: 8]=64 | ((dr+2)<<4) | ((dg+2)<<2) | (db+2); count_v=count_v+1;
          end else if(dg>=-32 && dg<=31 && dgr>=-8 && dgr<=7 && dgb>=-8 && dgb<=7) begin
            payload[8*count_v +: 8]=128 | (dg+32);
            payload[8*(count_v+1) +: 8]=((dgr+8)<<4) | (dgb+8); count_v=count_v+2;
          end else begin
            payload[8*count_v +: 32]={b[7:0],g[7:0],r[7:0],8'hfe}; count_v=count_v+4;
          end
        end
      end
      previous=pixel;
    end
    result={count_v[5:0],payload};
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
