module cryptography_pqc_kyber_kyber_montgomery_reduce (
    input  logic               clk,
    input  logic               rst,
    input  logic               in_valid,
    output logic               in_ready,
    input  logic signed [31:0] in_a,
    output logic               out_valid,
    input  logic               out_ready,
    output logic signed [15:0] out_t
);

    localparam logic [15:0] QINV_U16  = 16'd62209;
    localparam logic signed [31:0] KYBER_Q_S32 = 32'sd3329;

    logic busy;

    logic [15:0] a_lo;
    logic [31:0] mul_full;
    logic [15:0] mul_lo;
    logic signed [15:0] u_s16;
    logic signed [31:0] u_ext_s32;
    logic signed [31:0] uq_s32;
    logic signed [31:0] m_s32;
    logic signed [31:0] t_s32;

    wire input_accepted;
    wire output_consumed;

    assign output_consumed = out_valid & out_ready;
    assign input_accepted  = in_valid & in_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    always_comb begin
        a_lo      = in_a[15:0];
        mul_full  = a_lo * QINV_U16;
        mul_lo    = mul_full[15:0];
        u_s16     = mul_lo;
        u_ext_s32 = u_s16;
        uq_s32    = u_ext_s32 * KYBER_Q_S32;
        m_s32     = in_a - uq_s32;
        t_s32     = m_s32 >>> 16;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy  <= 1'b0;
            out_t <= 16'sd0;
        end else if (input_accepted) begin
            busy  <= 1'b1;
            out_t <= t_s32[15:0];
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule