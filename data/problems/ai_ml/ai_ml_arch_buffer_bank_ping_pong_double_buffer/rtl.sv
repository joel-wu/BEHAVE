module ai_ml_arch_buffer_bank_ping_pong_double_buffer #(
    parameter int N = 16,
    parameter int ELEM_W = 32,
    parameter int ROWS = 1,
    parameter int COLS = 1,
    parameter int SCALE_W = 32,
    parameter int ACT_W = 3,
    parameter int ADDR_W = 4,
    parameter int DATA_W = ROWS * COLS * ELEM_W,
    parameter int MASK_W = DATA_W / 8
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic                     in_is_read,
    input  logic [ADDR_W-1:0]        in_addr,
    input  logic [DATA_W-1:0]        in_data,
    input  logic                     in_acc,
    input  logic [MASK_W-1:0]        in_mask,
    input  logic [SCALE_W-1:0]       in_scale,
    input  logic [ELEM_W-1:0]        in_igelu_qb,
    input  logic [ELEM_W-1:0]        in_igelu_qc,
    input  logic [ELEM_W-1:0]        in_iexp_qln2,
    input  logic [ELEM_W-1:0]        in_iexp_qln2_inv,
    input  logic [ACT_W-1:0]         in_act,
    input  logic                     in_full,
    input  logic                     in_fromDMA,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [DATA_W-1:0]        out_data,
    output logic [SCALE_W-1:0]       out_scale,
    output logic [ELEM_W-1:0]        out_igelu_qb,
    output logic [ELEM_W-1:0]        out_igelu_qc,
    output logic [ELEM_W-1:0]        out_iexp_qln2,
    output logic [ELEM_W-1:0]        out_iexp_qln2_inv,
    output logic [ACT_W-1:0]         out_act,
    output logic                     out_fromDMA,
    output logic [1:0]               out_acc_bank_id
);

    logic [DATA_W-1:0] mem [0:N-1];

    logic busy;

    logic [DATA_W-1:0] replacement_word;
    logic [DATA_W-1:0] masked_new_word;
    logic [DATA_W-1:0] old_word_comb;

    logic signed [ELEM_W-1:0] old_elem_signed;
    logic signed [ELEM_W-1:0] in_elem_signed;
    logic signed [ELEM_W-1:0] sum_signed;

    integer i;
    integer j;

    wire input_accepted  = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    assign old_word_comb   = mem[in_addr];
    assign old_elem_signed = old_word_comb[ELEM_W-1:0];
    assign in_elem_signed  = in_data[ELEM_W-1:0];
    assign sum_signed      = old_elem_signed + in_elem_signed;

    always_comb begin
        replacement_word = in_data;
        if (in_acc) begin
            replacement_word = {{(DATA_W-ELEM_W){1'b0}}, sum_signed};
        end

        masked_new_word = old_word_comb;
        for (j = 0; j < MASK_W; j = j + 1) begin
            if (in_mask[j]) begin
                masked_new_word[j*8 +: 8] = replacement_word[j*8 +: 8];
            end
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy              <= 1'b0;
            out_data          <= '0;
            out_scale         <= '0;
            out_igelu_qb      <= '0;
            out_igelu_qc      <= '0;
            out_iexp_qln2     <= '0;
            out_iexp_qln2_inv <= '0;
            out_act           <= '0;
            out_fromDMA       <= 1'b0;
            out_acc_bank_id   <= 2'b00;
            for (i = 0; i < N; i = i + 1) begin
                mem[i] <= '0;
            end
        end else begin
            if (input_accepted) begin
                if (in_is_read) begin
                    out_data          <= mem[in_addr];
                    out_scale         <= in_scale;
                    out_igelu_qb      <= in_igelu_qb;
                    out_igelu_qc      <= in_igelu_qc;
                    out_iexp_qln2     <= in_iexp_qln2;
                    out_iexp_qln2_inv <= in_iexp_qln2_inv;
                    out_act           <= in_act;
                    out_fromDMA       <= in_fromDMA;
                    out_acc_bank_id   <= 2'b00;
                    busy              <= 1'b1;
                end else begin
                    mem[in_addr] <= masked_new_word;
                    busy         <= 1'b0;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule