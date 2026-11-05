module compression_jpeg_entropy_jpeg_dc_predictor #(
    parameter int MAX_COMPS = 4,
    parameter int COMP_IDX_W = 2
) (
    input  logic                    clk,
    input  logic                    rst,

    input  logic                    in_valid,
    output logic                    in_ready,
    input  logic                    in_restart,
    input  logic [COMP_IDX_W-1:0]   in_comp_idx,
    input  logic [31:0]             in_dc_diff,

    output logic                    out_valid,
    input  logic                    out_ready,
    output logic [31:0]             out_dc_value
);

logic busy;
logic [31:0] last_dc_val [0:MAX_COMPS-1];
localparam int MOD_W = (COMP_IDX_W > 32) ? COMP_IDX_W : 32;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

integer i;
integer ci_int;
logic [31:0] pred_val;
logic [31:0] next_dc_value;

always_ff @(posedge clk) begin
    if (rst) begin
        busy         <= 1'b0;
        out_dc_value <= 32'b0;
        for (i = 0; i < MAX_COMPS; i = i + 1) begin
            last_dc_val[i] <= 32'b0;
        end
    end else if (input_accepted) begin
        if (in_restart) begin
            for (i = 0; i < MAX_COMPS; i = i + 1) begin
                last_dc_val[i] <= 32'b0;
            end
        end

        if (MAX_COMPS != 0) begin
            ci_int = 32'(MOD_W'(in_comp_idx) % MOD_W'(MAX_COMPS));
            if (in_restart) begin
                pred_val = 32'b0;
            end else begin
                pred_val = last_dc_val[ci_int];
            end
            next_dc_value = pred_val + in_dc_diff;
            last_dc_val[ci_int] <= next_dc_value;
            out_dc_value <= next_dc_value;
        end else begin
            next_dc_value = in_dc_diff;
            out_dc_value <= next_dc_value;
        end

        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
