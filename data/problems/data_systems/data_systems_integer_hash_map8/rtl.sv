// SPDX-License-Identifier: MIT
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module HashMap8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [1:0] in_op,
    input logic [15:0] in_key,
    input logic [15:0] in_value,
    output logic [22:0] out_values
);

logic [127:0] keys,values,next_keys,next_values;
logic [15:0] flags,next_flags;
logic [22:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : map_operation
    integer hit,site,done,slot,flag,status_v,count_v;
    logic [15:0] payload;
    next_keys=keys; next_values=values; next_flags=flags;
    hit=-1; site=-1; done=0; slot=in_key & 7; flag=0;
    for(integer step=0;step<8;step=step+1) begin
      if(!done) begin
        flag=next_flags[2*slot +: 2];
        if(flag==0) begin if(site<0) site=slot; done=1; end
        else if(flag==2) site=slot;
        else if(next_keys[16*slot +: 16]==in_key) begin hit=slot; done=1; end
      end
      slot=(slot+step+1)&7;
    end
    payload=0; status_v=0;
    if(in_op==3) begin next_keys=0; next_values=0; next_flags=0; status_v=6; end
    else if(in_op==1) begin
      if(hit>=0) begin site=hit; status_v=3; end
      else if(site>=0) status_v=2;
      else status_v=5;
      if(site>=0) begin
        next_keys[16*site +: 16]=in_key; next_values[16*site +: 16]=in_value;
        next_flags[2*site +: 2]=1; payload=in_value;
      end
    end else if(hit>=0) begin
      payload=next_values[16*hit +: 16]; status_v=1;
      if(in_op==2) begin next_flags[2*hit +: 2]=2; status_v=4; end
    end
    count_v=0;
    for(integer i=0;i<8;i=i+1) if(next_flags[2*i +: 2]==1) count_v=count_v+1;
    result={count_v[3:0],status_v[2:0],payload};
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;
    keys<=0; values<=0; flags<=0;
  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;
    if(in_valid) begin keys<=next_keys; values<=next_values; flags<=next_flags; end
  end
end
endmodule
