module q15_fir_lattice_4stage (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [15:0] in_x,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [15:0] out_y,

    output logic        coeff_req_valid,
    output logic [1:0]  coeff_req_addr,
    input  logic        coeff_req_ready,
    input  logic        coeff_resp_valid,
    input  logic [15:0] coeff_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE     = 3'd0,
        ST_REQ      = 3'd1,
        ST_WAITRESP = 3'd2,
        ST_OUT      = 3'd3,
        ST_CALC     = 3'd4
    } state_t;

    state_t state_fsm;

    logic signed [15:0] arch_state0;
    logic signed [15:0] arch_state1;
    logic signed [15:0] arch_state2;
    logic signed [15:0] arch_state3;

    logic signed [15:0] in_x_reg;
    logic signed [15:0] g0_prev_reg;
    logic signed [15:0] g1_prev_reg;
    logic signed [15:0] g2_prev_reg;
    logic signed [15:0] g3_prev_reg;

    logic signed [15:0] coeff0;
    logic signed [15:0] coeff1;
    logic signed [15:0] coeff2;
    logic signed [15:0] coeff3;

    logic [1:0] coeff_idx;

    logic signed [15:0] out_y_reg;

    function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
        begin
            if (value > 32'sd32767) begin
                sat16 = 16'sd32767;
            end else if (value < -32'sd32768) begin
                sat16 = -16'sd32768;
            end else begin
                sat16 = value[15:0];
            end
        end
    endfunction

    function automatic logic signed [31:0] mul_q15(
        input logic signed [15:0] a,
        input logic signed [15:0] b
    );
        logic signed [31:0] prod;
        begin
            prod = a * b;
            mul_q15 = prod >>> 15;
        end
    endfunction

    logic signed [15:0] f0_w;
    logic signed [15:0] k1_w;
    logic signed [15:0] k2_w;
    logic signed [15:0] k3_w;
    logic signed [15:0] k4_w;
    logic signed [15:0] f1_w;
    logic signed [15:0] g1_w;
    logic signed [15:0] f2_w;
    logic signed [15:0] g2_w;
    logic signed [15:0] f3_w;
    logic signed [15:0] g3_w;
    logic signed [15:0] f4_w;

    // (-32768 * -32768) >> 15 is +32768: retain it until the sum saturates.
    logic signed [31:0] m1a_w;
    logic signed [31:0] m1b_w;
    logic signed [31:0] m2a_w;
    logic signed [31:0] m2b_w;
    logic signed [31:0] m3a_w;
    logic signed [31:0] m3b_w;
    logic signed [31:0] m4a_w;

    assign f0_w = in_x_reg;
    assign k1_w = coeff0;
    assign k2_w = coeff1;
    assign k3_w = coeff2;
    assign k4_w = coeff3;

    assign m1a_w = mul_q15(g0_prev_reg, k1_w);
    assign m1b_w = mul_q15(f0_w,      k1_w);
    assign f1_w  = sat16($signed(f0_w)       + $signed(m1a_w));
    assign g1_w  = sat16($signed(g0_prev_reg)+ $signed(m1b_w));

    assign m2a_w = mul_q15(g1_prev_reg, k2_w);
    assign m2b_w = mul_q15(f1_w,        k2_w);
    assign f2_w  = sat16($signed(f1_w)       + $signed(m2a_w));
    assign g2_w  = sat16($signed(g1_prev_reg)+ $signed(m2b_w));

    assign m3a_w = mul_q15(g2_prev_reg, k3_w);
    assign m3b_w = mul_q15(f2_w,        k3_w);
    assign f3_w  = sat16($signed(f2_w)       + $signed(m3a_w));
    assign g3_w  = sat16($signed(g2_prev_reg)+ $signed(m3b_w));

    assign m4a_w = mul_q15(g3_prev_reg, k4_w);
    assign f4_w  = sat16($signed(f3_w)       + $signed(m4a_w));

    always_comb begin
        in_ready        = 1'b0;
        out_valid       = 1'b0;
        out_y           = 16'h0000;
        coeff_req_valid = 1'b0;
        coeff_req_addr  = 2'b00;

        if (!rst) begin
            unique case (state_fsm)
                ST_IDLE: begin
                    in_ready = 1'b1;
                end

                ST_REQ: begin
                    coeff_req_valid = 1'b1;
                    coeff_req_addr  = coeff_idx;
                end

                ST_WAITRESP, ST_CALC: begin
                end

                ST_OUT: begin
                    out_valid = 1'b1;
                    out_y     = out_y_reg;
                end
            endcase
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state_fsm   <= ST_IDLE;
            arch_state0 <= 16'sd0;
            arch_state1 <= 16'sd0;
            arch_state2 <= 16'sd0;
            arch_state3 <= 16'sd0;
            in_x_reg    <= 16'sd0;
            g0_prev_reg <= 16'sd0;
            g1_prev_reg <= 16'sd0;
            g2_prev_reg <= 16'sd0;
            g3_prev_reg <= 16'sd0;
            coeff0      <= 16'sd0;
            coeff1      <= 16'sd0;
            coeff2      <= 16'sd0;
            coeff3      <= 16'sd0;
            coeff_idx   <= 2'd0;
            out_y_reg   <= 16'sd0;
        end else begin
            unique case (state_fsm)
                ST_IDLE: begin
                    if (in_valid) begin
                        in_x_reg    <= in_x;
                        g0_prev_reg <= arch_state0;
                        g1_prev_reg <= arch_state1;
                        g2_prev_reg <= arch_state2;
                        g3_prev_reg <= arch_state3;
                        coeff_idx   <= 2'd0;
                        state_fsm   <= ST_REQ;
                    end
                end

                ST_REQ: begin
                    if (coeff_req_ready) begin
                        state_fsm <= ST_WAITRESP;
                    end
                end

                ST_WAITRESP: begin
                    if (coeff_resp_valid) begin
                        unique case (coeff_idx)
                            2'd0: coeff0 <= coeff_resp_rdata;
                            2'd1: coeff1 <= coeff_resp_rdata;
                            2'd2: coeff2 <= coeff_resp_rdata;
                            2'd3: coeff3 <= coeff_resp_rdata;
                        endcase

                        if (coeff_idx == 2'd3) begin
                            state_fsm <= ST_CALC;
                        end else begin
                            coeff_idx <= coeff_idx + 2'd1;
                            state_fsm <= ST_REQ;
                        end
                    end
                end

                ST_CALC: begin
                    // All four registered response values are now available.
                    out_y_reg   <= f4_w;
                    arch_state0 <= f0_w;
                    arch_state1 <= g1_w;
                    arch_state2 <= g2_w;
                    arch_state3 <= g3_w;
                    state_fsm   <= ST_OUT;
                end

                ST_OUT: begin
                    if (out_ready)
                        state_fsm <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
