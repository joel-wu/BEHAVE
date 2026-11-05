// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module DualDelayHysteresis (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [0:0] in_request,
    input logic [3:0] in_rise_delay,
    input logic [3:0] in_fall_delay,
    output logic [0:0] out_values
);

logic level, requested, next_level, next_requested;
logic [3:0] age, next_age;
logic [0:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : confirmation_timer
    next_level=level; next_requested=requested; next_age=age;
    if(in_request==level) begin next_requested=level; next_age=0; end
    else begin
      if(in_request!=requested) begin next_requested=in_request; next_age=0; end
      else if(age<15) next_age=age+1;
      if(next_age >= (level ? in_fall_delay : in_rise_delay)) begin
        next_level=next_requested; next_age=0;
      end
    end
    result=next_level;
  end
end
assign in_ready = !out_valid || out_ready;
always_ff @(posedge clk) begin
  if (rst) begin
    out_valid <= 0;
    out_values <= 0;
    level <= 0; requested <= 0; age <= 0;
  end else if (in_ready) begin
    out_valid <= in_valid;
    if (in_valid) out_values <= result;
    if(in_valid) begin level<=next_level; requested<=next_requested; age<=next_age; end
  end
end
endmodule
