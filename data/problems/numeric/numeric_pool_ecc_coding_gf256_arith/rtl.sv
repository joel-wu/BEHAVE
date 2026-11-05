module xor_summer64x4 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_finalize,
    input  logic        in_dest_init,
    input  logic [63:0] in_dest_word,
    input  logic [2:0]  in_src_count,
    input  logic [63:0] in_src0_word,
    input  logic [63:0] in_src1_word,
    input  logic [63:0] in_src2_word,
    input  logic [63:0] in_src3_word,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [63:0] out_dest_word,
    output logic        out_waiting_valid,
    output logic [63:0] out_waiting_word
);

    logic        busy;
    logic [63:0] dest_state;
    logic        waiting_valid_state;
    logic [63:0] waiting_word_state;

    logic [63:0] next_dest;
    logic        next_waiting_valid;
    logic [63:0] next_waiting_word;

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        next_dest          = dest_state;
        next_waiting_valid = waiting_valid_state;
        next_waiting_word  = waiting_word_state;

        if (in_dest_init) begin
            next_dest = in_dest_word;
            next_waiting_valid = 0;
            next_waiting_word = 0;
        end

        if (in_src_count >= 3'd1) begin
            if (next_waiting_valid) begin
                next_dest          = next_dest ^ next_waiting_word ^ in_src0_word;
                next_waiting_valid = 1'b0;
            end else begin
                next_waiting_word  = in_src0_word;
                next_waiting_valid = 1'b1;
            end
        end

        if (in_src_count >= 3'd2) begin
            if (next_waiting_valid) begin
                next_dest          = next_dest ^ next_waiting_word ^ in_src1_word;
                next_waiting_valid = 1'b0;
            end else begin
                next_waiting_word  = in_src1_word;
                next_waiting_valid = 1'b1;
            end
        end

        if (in_src_count >= 3'd3) begin
            if (next_waiting_valid) begin
                next_dest          = next_dest ^ next_waiting_word ^ in_src2_word;
                next_waiting_valid = 1'b0;
            end else begin
                next_waiting_word  = in_src2_word;
                next_waiting_valid = 1'b1;
            end
        end

        if (in_src_count >= 3'd4) begin
            if (next_waiting_valid) begin
                next_dest          = next_dest ^ next_waiting_word ^ in_src3_word;
                next_waiting_valid = 1'b0;
            end else begin
                next_waiting_word  = in_src3_word;
                next_waiting_valid = 1'b1;
            end
        end

        if (in_finalize && next_waiting_valid) begin
            next_dest          = next_dest ^ next_waiting_word;
            next_waiting_valid = 1'b0;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy               <= 1'b0;
            dest_state         <= 64'h0000000000000000;
            waiting_valid_state<= 1'b0;
            waiting_word_state <= 64'h0000000000000000;
            out_dest_word      <= 64'h0000000000000000;
            out_waiting_valid  <= 1'b0;
            out_waiting_word   <= 64'h0000000000000000;
        end else if (input_accepted) begin
            busy                <= 1'b1;
            dest_state          <= next_dest;
            waiting_valid_state <= next_waiting_valid;
            waiting_word_state  <= next_waiting_word;
            out_dest_word       <= next_dest;
            out_waiting_valid   <= next_waiting_valid;
            out_waiting_word    <= next_waiting_word;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
