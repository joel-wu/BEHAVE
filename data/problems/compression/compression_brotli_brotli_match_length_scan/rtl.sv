module compression_brotli_brotli_match_length_scan (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [255:0] in_s1,
    input  logic [255:0] in_s2,
    input  logic [5:0]   in_limit,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [5:0]   out_matched
);

logic busy;
logic [5:0] result_matched;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;
assign out_matched = result_matched;

function automatic logic [5:0] compute_matched(
    input logic [255:0] s1,
    input logic [255:0] s2,
    input logic [5:0]   limit
);
    logic [5:0] limit_eff;
    logic [5:0] matched;
    logic matching;
begin
    limit_eff = (limit < 6'd32) ? limit : 6'd32;
    matched = 6'd0;
    matching = 1'b1;
    for (int i = 0; i < 32; i++) begin
        if (matching && i < int'(limit_eff)) begin
            if (s1[8*i +: 8] == s2[8*i +: 8])
                matched = matched + 6'd1;
            else
                matching = 1'b0;
        end
    end

    compute_matched = matched;
end
endfunction

always_ff @(posedge clk or posedge rst) begin
    if (rst) begin
        busy           <= 1'b0;
        result_matched <= 6'd0;
    end else if (input_accepted) begin
        result_matched <= compute_matched(in_s1, in_s2, in_limit);
        busy           <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
