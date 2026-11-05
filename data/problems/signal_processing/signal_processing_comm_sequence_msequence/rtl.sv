module signal_processing_comm_sequence_msequence (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [1:0]  in_op,
    input  logic [4:0]  in_m,
    input  logic [30:0] in_g,
    input  logic [30:0] in_a,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [30:0] out_bit,
    output logic [30:0] out_state,
    output logic [30:0] out_n
);

    logic [4:0] m_reg;
    logic [30:0] g_reg, seed_reg, state_reg;
    logic busy;
    logic [4:0] next_m;
    logic [30:0] next_g, next_seed, next_state, next_n;
    logic next_bit;

    wire input_accepted = in_valid & in_ready;
    wire output_consumed = out_valid & out_ready;
    assign in_ready = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        next_m = m_reg;
        next_g = g_reg;
        next_seed = seed_reg;
        next_state = state_reg;
        next_bit = 1'b0;
        case (in_op)
            2'd0: if (in_m >= 5'd2) begin
                next_m = in_m;
                next_g = in_g;
                next_seed = in_a;
                next_state = in_a;
            end
            2'd1: begin
                next_bit = ^(state_reg & g_reg);
                next_state = ((state_reg << 1) | {30'd0,next_bit}) &
                             ((31'd1 << m_reg) - 31'd1);
            end
            2'd2: next_state = seed_reg;
            default: begin end
        endcase
        next_n = (31'd1 << next_m) - 31'd1;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            m_reg <= 5'd2;
            g_reg <= 31'd3;
            seed_reg <= 31'd1;
            state_reg <= 31'd1;
            busy <= 1'b0;
            out_bit <= 31'd0;
            out_state <= 31'd0;
            out_n <= 31'd0;
        end else if (input_accepted) begin
            m_reg <= next_m;
            g_reg <= next_g;
            seed_reg <= next_seed;
            state_reg <= next_state;
            busy <= 1'b1;
            out_bit <= {30'd0,next_bit};
            out_state <= next_state;
            out_n <= next_n;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end
endmodule
