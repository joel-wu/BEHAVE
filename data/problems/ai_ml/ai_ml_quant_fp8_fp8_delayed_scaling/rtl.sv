module ai_ml_quant_fp8_fp8_delayed_scaling #(
    parameter int unsigned HISTORY_LEN = 4,
    parameter int unsigned AMAX_WIDTH  = 32,
    parameter int unsigned SCALE_WIDTH = 64
) (
    input  logic                         clk,
    input  logic                         rst,

    input  logic                         in_valid,
    output logic                         in_ready,
    input  logic [AMAX_WIDTH-1:0]        in_amax,
    input  logic [SCALE_WIDTH-1:0]       in_scale,
    input  logic [AMAX_WIDTH-1:0]        in_history_0,
    input  logic [AMAX_WIDTH-1:0]        in_history_1,
    input  logic [AMAX_WIDTH-1:0]        in_history_2,
    input  logic [AMAX_WIDTH-1:0]        in_history_3,

    output logic                         out_valid,
    input  logic                         out_ready,
    output logic [SCALE_WIDTH-1:0]       out_scale,
    output logic [SCALE_WIDTH-1:0]       out_scale_inv,
    output logic [AMAX_WIDTH-1:0]        out_history_0,
    output logic [AMAX_WIDTH-1:0]        out_history_1,
    output logic [AMAX_WIDTH-1:0]        out_history_2,
    output logic [AMAX_WIDTH-1:0]        out_history_3
);

    localparam logic [63:0] SCALE_NUMERATOR = 64'd126100789566373888; // 448 * 2^48
    localparam logic [64:0] RECIP_NUMERATOR = 65'h10000000000000000;

    logic busy;
    logic [AMAX_WIDTH-1:0] selected_amax_next;
    logic [AMAX_WIDTH-1:0] current_amax;
    logic [64:0] reciprocal_wide;
    logic [SCALE_WIDTH-1:0] out_scale_next;
    logic [SCALE_WIDTH-1:0] out_scale_inv_next;
    logic [AMAX_WIDTH-1:0] out_history_0_next;
    logic [AMAX_WIDTH-1:0] out_history_1_next;
    logic [AMAX_WIDTH-1:0] out_history_2_next;
    logic [AMAX_WIDTH-1:0] out_history_3_next;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        current_amax = in_amax > in_history_0 ? in_amax : in_history_0;
        out_history_0_next = '0;
        out_history_1_next = in_history_2;
        out_history_2_next = in_history_3;
        out_history_3_next = current_amax;

        selected_amax_next = current_amax;
        if (in_history_1 > selected_amax_next) begin
            selected_amax_next = in_history_1;
        end
        if (in_history_2 > selected_amax_next) begin
            selected_amax_next = in_history_2;
        end
        if (in_history_3 > selected_amax_next) begin
            selected_amax_next = in_history_3;
        end

        if (selected_amax_next == {AMAX_WIDTH{1'b0}}) begin
            out_scale_next = in_scale;
        end else begin
            out_scale_next = SCALE_NUMERATOR / 64'(selected_amax_next);
        end

        reciprocal_wide = '0;
        if (out_scale_next == {SCALE_WIDTH{1'b0}}) begin
            out_scale_inv_next = {SCALE_WIDTH{1'b0}};
        end else begin
            reciprocal_wide = RECIP_NUMERATOR / 65'(out_scale_next);
            out_scale_inv_next = reciprocal_wide[64] ? 64'hffff_ffff_ffff_ffff : reciprocal_wide[63:0];
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy          <= 1'b0;
            out_scale     <= {SCALE_WIDTH{1'b0}};
            out_scale_inv <= {SCALE_WIDTH{1'b0}};
            out_history_0 <= {AMAX_WIDTH{1'b0}};
            out_history_1 <= {AMAX_WIDTH{1'b0}};
            out_history_2 <= {AMAX_WIDTH{1'b0}};
            out_history_3 <= {AMAX_WIDTH{1'b0}};
        end else if (input_accepted) begin
            busy          <= 1'b1;
            out_scale     <= out_scale_next;
            out_scale_inv <= out_scale_inv_next;
            out_history_0 <= out_history_0_next;
            out_history_1 <= out_history_1_next;
            out_history_2 <= out_history_2_next;
            out_history_3 <= out_history_3_next;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
