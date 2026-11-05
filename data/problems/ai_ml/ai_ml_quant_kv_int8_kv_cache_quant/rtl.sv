module ai_ml_quant_kv_int8_kv_cache_quant #(
    parameter int HEAD_SIZE = 128,
    parameter int SLOT_BITS = 16
) (
    input  logic                     clk,
    input  logic                     rst,

    input  logic                     in_valid,
    output logic                     in_ready,
    input  logic [SLOT_BITS-1:0]     in_slot,
    input  logic [16*HEAD_SIZE-1:0]  in_key,
    input  logic [16*HEAD_SIZE-1:0]  in_value,
    input  logic [31:0]              in_k_scale_num,
    input  logic [31:0]              in_v_scale_num,

    output logic                     out_valid,
    input  logic                     out_ready,
    output logic [SLOT_BITS-1:0]     out_slot,
    output logic [8*HEAD_SIZE-1:0]   out_key_q,
    output logic [8*HEAD_SIZE-1:0]   out_value_q
);

    logic busy;
    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = ~busy | output_consumed;
    assign out_valid = busy;

    function automatic logic signed [7:0] quant_elem(
        input logic signed [15:0] x,
        input logic [31:0] s
    );
        logic [15:0] ax;
        logic [47:0] numer;
        logic [47:0] denom;
        logic [47:0] q_mag_u;
        logic signed [47:0] q_signed_w;
        logic signed [7:0] result_q;
    begin
        result_q   = 8'sd0;
        ax         = 16'd0;
        numer      = 48'd0;
        denom      = 48'd0;
        q_mag_u    = 48'd0;
        q_signed_w = 48'sd0;

        if (s == 32'd0) begin
            result_q = 8'sd0;
        end else begin
            if (x >= 16'sd0) begin
                ax = x[15:0];
            end else begin
                ax = -x;
            end

            numer   = ({32'd0, ax} << 1) + {16'd0, s};
            denom   = {16'd0, s} << 1;
            q_mag_u = numer / denom;

            if (x >= 16'sd0) begin
                q_signed_w = $signed(q_mag_u);
            end else begin
                q_signed_w = -$signed(q_mag_u);
            end

            if (q_signed_w > 48'sd127) result_q = 8'sd127;
            else if (q_signed_w < -48'sd128) result_q = -8'sd128;
            else result_q = q_signed_w[7:0];
        end

        quant_elem = result_q;
    end
    endfunction

    integer i;
    logic signed [15:0] key_elem;
    logic signed [15:0] value_elem;
    logic signed [7:0]  key_q_elem;
    logic signed [7:0]  value_q_elem;
    logic [8*HEAD_SIZE-1:0] next_out_key_q;
    logic [8*HEAD_SIZE-1:0] next_out_value_q;

    always_comb begin
        next_out_key_q   = '0;
        next_out_value_q = '0;

        for (i = 0; i < HEAD_SIZE; i = i + 1) begin
            key_elem     = $signed(in_key[i*16 +: 16]);
            value_elem   = $signed(in_value[i*16 +: 16]);
            key_q_elem   = quant_elem(key_elem, in_k_scale_num);
            value_q_elem = quant_elem(value_elem, in_v_scale_num);

            next_out_key_q[i*8 +: 8]   = key_q_elem[7:0];
            next_out_value_q[i*8 +: 8] = value_q_elem[7:0];
        end
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy        <= 1'b0;
            out_slot    <= '0;
            out_key_q   <= '0;
            out_value_q <= '0;
        end else if (input_accepted) begin
            busy        <= 1'b1;
            out_slot    <= in_slot;
            out_key_q   <= next_out_key_q;
            out_value_q <= next_out_value_q;
        end else if (output_consumed) begin
            busy <= 1'b0;
        end
    end

endmodule
