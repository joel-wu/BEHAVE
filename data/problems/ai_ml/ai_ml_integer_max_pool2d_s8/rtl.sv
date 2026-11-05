// SPDX-License-Identifier: Apache-2.0
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module MaxPool2DS8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [255:0] in_data,
    output logic [63:0] out_values
);
function automatic signed [31:0] s8(input logic [7:0] x);
  s8 = {{24{x[7]}}, x};
endfunction
logic [63:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  for (integer h=0; h<2; h=h+1)
    for (integer w=0; w<2; w=w+1)
      for (integer c=0; c<2; c=c+1) begin
        value = -128;
        for (integer i=0; i<2; i=i+1)
          for (integer j=0; j<2; j=j+1) begin
            sample = s8(in_data[8*((4*(2*h+i)+2*w+j)*2+c) +: 8]);
            if (sample > value) value = sample;
          end

        result[8*((2*h+w)*2+c) +: 8] = 8'(value);
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
