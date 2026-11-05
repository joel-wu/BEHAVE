module q15_abs_stream (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_sample,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_sample
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [15:0] q15_abs(input logic [15:0] sample);
    logic signed [15:0] x;
    begin
        x = sample;
        if (x > 16'sd0) begin
            q15_abs = sample;
        end else if (x == 16'sd0) begin
            q15_abs = 16'h0000;
        end else if (sample == 16'h8000) begin
            q15_abs = 16'h7FFF;
        end else begin
            q15_abs = -x;
        end
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_sample <= 16'h0000;
    end else if (input_accepted) begin
        busy       <= 1'b1;
        out_sample <= q15_abs(in_sample);
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule