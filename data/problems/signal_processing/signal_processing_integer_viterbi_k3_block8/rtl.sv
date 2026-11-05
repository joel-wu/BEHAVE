// SPDX-License-Identifier: BSD-3-Clause
// Integer/fixed-shape adaptation; modified from the cited source operations.
// Source attribution: source.json and the accompanying license notices.
module ViterbiK3Block8 (
    input logic clk, rst, in_valid, out_ready,
    output logic in_ready, out_valid,
    input logic [15:0] in_codeword,
    output logic [12:0] out_values
);


logic [12:0] result;
integer signed value, sample;
logic signed [63:0] wide;
always_comb begin
  result='0; value=0; sample=0; wide=0;
  begin : survivor_search
    integer metrics[0:3], paths[0:3], next_metrics[0:3], next_paths[0:3];
    integer received, history, first_bit, second_bit, difference, metric, path, target;
    integer best_metric, best_path;
    received=0; history=0; first_bit=0; second_bit=0; difference=0;
    metric=0; path=0; target=0; best_metric=17; best_path=256;
    for(integer s=0;s<4;s=s+1) begin
      metrics[s]=(s==0)?0:17; paths[s]=0; next_metrics[s]=17; next_paths[s]=256;
    end
    for(integer t=0;t<8;t=t+1) begin
      for(integer s=0;s<4;s=s+1) begin next_metrics[s]=17; next_paths[s]=256; end
      received=in_codeword[2*t +: 2];
      for(integer s=0;s<4;s=s+1) if(metrics[s]<17) begin
        for(integer bit_value=0;bit_value<2;bit_value=bit_value+1) begin
          history=(s<<1)|bit_value;
          first_bit=(history^(history>>1)^(history>>2))&1;
          second_bit=(history^(history>>2))&1;
          difference=received^(first_bit|(second_bit<<1));
          metric=metrics[s]+(difference&1)+((difference>>1)&1);
          path=(paths[s]<<1)|bit_value; target=history&3;
          if(metric<next_metrics[target] || (metric==next_metrics[target] && path<next_paths[target])) begin
            next_metrics[target]=metric; next_paths[target]=path;
          end
        end
      end
      for(integer s=0;s<4;s=s+1) begin metrics[s]=next_metrics[s]; paths[s]=next_paths[s]; end
    end
    for(integer s=0;s<4;s=s+1)
      if(metrics[s]<best_metric || (metrics[s]==best_metric && paths[s]<best_path)) begin
        best_metric=metrics[s]; best_path=paths[s];
      end
    result={best_metric[4:0],best_path[7:0]};
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
