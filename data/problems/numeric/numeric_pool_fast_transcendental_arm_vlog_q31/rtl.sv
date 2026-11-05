module q31_natural_log (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_src,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_dst
);

    localparam logic [31:0] LOG_Q31_INVLOG2EXP = 32'h58B90BFB;
    localparam logic [31:0] INC_INIT           = 32'h02000000;
    localparam logic [31:0] THRESHOLD          = 32'h80000000;

    logic        busy;
    logic [31:0] x_reg;
    logic [31:0] y_reg;
    logic [31:0] inc_reg;
    logic [5:0]  norm_reg;
    logic [5:0]  iter_reg;

    logic [31:0] out_dst_reg;

    wire input_accepted;
    wire output_consumed;
    logic done;

    logic [63:0] xx_prod;
    logic [31:0] x_sq;
    logic [31:0] x_next;
    logic [31:0] y_next;
    logic [31:0] inc_next;

    logic signed [32:0] tmp_signed;
    logic signed [63:0] mul_signed;
    logic [31:0] result_bits;

    function automatic logic [5:0] leading_zero_count32(input logic [31:0] v);
        begin
            if (v[31])         leading_zero_count32 = 6'd0;
            else if (v[30])    leading_zero_count32 = 6'd1;
            else if (v[29])    leading_zero_count32 = 6'd2;
            else if (v[28])    leading_zero_count32 = 6'd3;
            else if (v[27])    leading_zero_count32 = 6'd4;
            else if (v[26])    leading_zero_count32 = 6'd5;
            else if (v[25])    leading_zero_count32 = 6'd6;
            else if (v[24])    leading_zero_count32 = 6'd7;
            else if (v[23])    leading_zero_count32 = 6'd8;
            else if (v[22])    leading_zero_count32 = 6'd9;
            else if (v[21])    leading_zero_count32 = 6'd10;
            else if (v[20])    leading_zero_count32 = 6'd11;
            else if (v[19])    leading_zero_count32 = 6'd12;
            else if (v[18])    leading_zero_count32 = 6'd13;
            else if (v[17])    leading_zero_count32 = 6'd14;
            else if (v[16])    leading_zero_count32 = 6'd15;
            else if (v[15])    leading_zero_count32 = 6'd16;
            else if (v[14])    leading_zero_count32 = 6'd17;
            else if (v[13])    leading_zero_count32 = 6'd18;
            else if (v[12])    leading_zero_count32 = 6'd19;
            else if (v[11])    leading_zero_count32 = 6'd20;
            else if (v[10])    leading_zero_count32 = 6'd21;
            else if (v[9])     leading_zero_count32 = 6'd22;
            else if (v[8])     leading_zero_count32 = 6'd23;
            else if (v[7])     leading_zero_count32 = 6'd24;
            else if (v[6])     leading_zero_count32 = 6'd25;
            else if (v[5])     leading_zero_count32 = 6'd26;
            else if (v[4])     leading_zero_count32 = 6'd27;
            else if (v[3])     leading_zero_count32 = 6'd28;
            else if (v[2])     leading_zero_count32 = 6'd29;
            else if (v[1])     leading_zero_count32 = 6'd30;
            else if (v[0])     leading_zero_count32 = 6'd31;
            else               leading_zero_count32 = 6'd32;
        end
    endfunction

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign in_ready        = ~busy | output_consumed;
    assign out_valid       = busy & done;
    assign out_dst         = out_dst_reg;
    assign done            = (iter_reg == 6'd31);

    always_comb begin
        xx_prod = {32'b0, x_reg} * {32'b0, x_reg};
        x_sq    = xx_prod[61:30];

        if (x_sq >= THRESHOLD) begin
            y_next   = y_reg + inc_reg;
            x_next   = x_sq >> 1;
        end else begin
            y_next   = y_reg;
            x_next   = x_sq;
        end

        inc_next = inc_reg >> 1;

        tmp_signed = $signed({1'b0, y_reg}) - $signed({1'b0, norm_reg, 26'b0});
        mul_signed = tmp_signed * $signed(LOG_Q31_INVLOG2EXP);
        result_bits = mul_signed >>> 31;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            busy       <= 1'b0;
            x_reg      <= 32'b0;
            y_reg      <= 32'b0;
            inc_reg    <= 32'b0;
            norm_reg   <= 6'b0;
            iter_reg   <= 6'b0;
            out_dst_reg <= 32'b0;
        end else begin
            if (input_accepted) begin
                logic [5:0] c;
                c = leading_zero_count32(in_src);

                if (c == 6'd0) begin
                    x_reg <= in_src >> 1;
                end else begin
                    x_reg <= in_src << (c - 6'd1);
                end

                y_reg       <= 32'b0;
                inc_reg     <= INC_INIT;
                norm_reg    <= c;
                iter_reg    <= 6'd0;
                out_dst_reg <= 32'b0;
                busy        <= 1'b1;
            end else if (busy && !done) begin
                if (iter_reg == 6'd30) begin
                    logic signed [32:0] final_tmp;
                    logic signed [63:0] final_mul;
                    logic [31:0] final_result;
                    logic [31:0] final_y;

                    if (x_sq >= THRESHOLD) begin
                        final_y = y_reg + inc_reg;
                    end else begin
                        final_y = y_reg;
                    end

                    final_tmp    = $signed({1'b0, final_y}) - $signed({1'b0, norm_reg, 26'b0});
                    final_mul    = final_tmp * $signed(LOG_Q31_INVLOG2EXP);
                    final_result = final_mul >>> 31;

                    x_reg       <= x_next;
                    y_reg       <= y_next;
                    inc_reg     <= inc_next;
                    iter_reg    <= 6'd31;
                    out_dst_reg <= final_result;
                    busy        <= 1'b1;
                end else begin
                    x_reg    <= x_next;
                    y_reg    <= y_next;
                    inc_reg  <= inc_next;
                    iter_reg <= iter_reg + 6'd1;
                    busy     <= 1'b1;
                end
            end else if (output_consumed) begin
                busy <= 1'b0;
            end
        end
    end

endmodule