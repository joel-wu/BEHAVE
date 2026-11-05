module signal_processing_comm_sequence_bsequence #(
    parameter int unsigned NUM_BITS = 32
) (
    input  logic                             clk,
    input  logic                             rst,

    input  logic                             in_valid,
    output logic                             in_ready,
    input  logic [3:0]                       in_op,
    input  logic                             in_bit,
    input  logic [((NUM_BITS > 1) ? $clog2(NUM_BITS) : 1)-1:0]      in_index,
    input  logic [NUM_BITS-1:0]              in_sequence,

    output logic                             out_valid,
    input  logic                             out_ready,
    output logic [NUM_BITS-1:0]              out_sequence,
    output logic                             out_bit,
    output logic [$clog2(NUM_BITS+1)-1:0]    out_count,
    output logic [$clog2(NUM_BITS+1)-1:0]    out_corr
);

    localparam int unsigned COUNT_W = $clog2(NUM_BITS+1);

    logic [NUM_BITS-1:0] state_sequence;
    logic                busy;

    logic [NUM_BITS-1:0] next_out_sequence;
    logic                next_out_bit;
    logic [COUNT_W-1:0]  next_out_count;
    logic [COUNT_W-1:0]  next_out_corr;
    logic [NUM_BITS-1:0] next_state_sequence;

    wire input_accepted;
    wire output_consumed;

    integer idx;

    function automatic [COUNT_W-1:0] popcount_bits(input logic [NUM_BITS-1:0] value);
        integer j;
        reg [COUNT_W-1:0] acc;
        begin
            acc = '0;
            for (j = 0; j < NUM_BITS; j = j + 1) begin
                acc = acc + value[j];
            end
            popcount_bits = acc;
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        next_state_sequence = state_sequence;
        next_out_sequence   = state_sequence;
        next_out_bit        = 1'b0;
        next_out_count      = popcount_bits(state_sequence);
        next_out_corr       = '0;

        unique case (in_op)
            4'd0: begin
                next_state_sequence = '0;
                next_out_sequence   = '0;
                next_out_bit        = 1'b0;
                next_out_count      = '0;
                next_out_corr       = '0;
            end

            4'd1: begin
                next_state_sequence = in_sequence;
                next_out_sequence   = in_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(in_sequence);
                next_out_corr       = COUNT_W'(NUM_BITS);
            end

            4'd2: begin
                next_out_bit        = in_bit;
                next_state_sequence = ((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},in_bit});
                next_out_sequence   = ((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},in_bit});
                next_out_count      = popcount_bits(((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},in_bit}));
                next_out_corr       = '0;
            end

            4'd3: begin
                next_out_bit        = state_sequence[NUM_BITS-1];
                next_state_sequence = ((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},state_sequence[NUM_BITS-1]});
                next_out_sequence   = ((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},state_sequence[NUM_BITS-1]});
                next_out_count      = popcount_bits(((state_sequence << 1) | {{(NUM_BITS-1){1'b0}},state_sequence[NUM_BITS-1]}));
                next_out_corr       = '0;
            end

            4'd4: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence ^ in_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(state_sequence ^ in_sequence);
                next_out_corr       = '0;
            end

            4'd5: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence & in_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(state_sequence & in_sequence);
                next_out_corr       = '0;
            end

            4'd6: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(state_sequence);
                next_out_corr       = '0;
            end

            4'd7: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence;
                next_out_bit        = (in_index < NUM_BITS) ? state_sequence[in_index] : 1'b0;
                next_out_count      = popcount_bits(state_sequence);
                next_out_corr       = '0;
            end

            4'd8: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(state_sequence);
                next_out_corr       = COUNT_W'(NUM_BITS) - popcount_bits(state_sequence ^ in_sequence);
            end

            default: begin
                next_state_sequence = state_sequence;
                next_out_sequence   = state_sequence;
                next_out_bit        = 1'b0;
                next_out_count      = popcount_bits(state_sequence);
                next_out_corr       = '0;
            end
        endcase
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            state_sequence <= '0;
            out_sequence   <= '0;
            out_bit        <= 1'b0;
            out_count      <= '0;
            out_corr       <= '0;
            busy           <= 1'b0;
        end else if (input_accepted) begin
            state_sequence <= next_state_sequence;
            out_sequence   <= next_out_sequence;
            out_bit        <= next_out_bit;
            out_count      <= next_out_count;
            out_corr       <= next_out_corr;
            busy           <= 1'b1;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
