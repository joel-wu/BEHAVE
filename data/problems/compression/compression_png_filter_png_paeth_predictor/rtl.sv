module compression_png_filter_png_paeth_predictor (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [7:0]  in_filtered_byte,
    input  logic [7:0]  in_left_byte,
    input  logic [7:0]  in_up_byte,
    input  logic [7:0]  in_up_left_byte,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_recon_byte,
    output logic [7:0]  out_predictor_byte
);

    logic busy;

    logic [7:0] predictor_next;
    logic [8:0] recon_sum_next;

    logic signed [9:0] a_s;
    logic signed [9:0] b_s;
    logic signed [9:0] c_s;
    logic signed [10:0] p_s;
    logic signed [10:0] pa0_s;
    logic signed [10:0] pb0_s;
    logic signed [10:0] pc0_s;
    logic signed [10:0] pa_s;
    logic signed [10:0] pb_s;
    logic signed [10:0] pc_s;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a_s = $signed({2'b00, in_left_byte});
        b_s = $signed({2'b00, in_up_byte});
        c_s = $signed({2'b00, in_up_left_byte});

        p_s   = a_s + b_s - c_s;
        pa0_s = p_s - a_s;
        pb0_s = p_s - b_s;
        pc0_s = p_s - c_s;

        if (pa0_s >= 0)
            pa_s = pa0_s;
        else
            pa_s = -pa0_s;

        if (pb0_s >= 0)
            pb_s = pb0_s;
        else
            pb_s = -pb0_s;

        if (pc0_s >= 0)
            pc_s = pc0_s;
        else
            pc_s = -pc0_s;

        if ((pa_s <= pb_s) && (pa_s <= pc_s))
            predictor_next = in_left_byte;
        else if (pb_s <= pc_s)
            predictor_next = in_up_byte;
        else
            predictor_next = in_up_left_byte;

        recon_sum_next = {1'b0, in_filtered_byte} + {1'b0, predictor_next};
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy               <= 1'b0;
            out_recon_byte     <= 8'h00;
            out_predictor_byte <= 8'h00;
        end else if (input_accepted) begin
            busy               <= 1'b1;
            out_predictor_byte <= predictor_next;
            out_recon_byte     <= recon_sum_next[7:0];
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule