module q31_to_float32 (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_q31,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_fp32
);

logic busy;

wire input_accepted;
wire output_consumed;

logic        sign_c;
logic [31:0] magnitude_c;
logic [5:0]  highest_c;
logic [7:0]  exponent_c;
logic [22:0] fraction_c;
logic [31:0] out_fp32_c;
logic [31:0] shifted_c;
logic [31:0] remainder_c;
logic [31:0] halfway_c;
logic [5:0] round_shift_c;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    sign_c      = 1'b0;
    magnitude_c = 32'd0;
    highest_c   = 6'd0;
    exponent_c  = 8'd0;
    fraction_c  = 23'd0;
    out_fp32_c  = 32'd0;
    shifted_c   = 32'd0;
    remainder_c = 32'd0;
    halfway_c   = 32'd0;
    round_shift_c = 6'd0;

    if (in_q31 != 32'd0) begin
        sign_c = in_q31[31];

        if (in_q31[31]) begin
            magnitude_c = (~in_q31) + 32'd1;
        end else begin
            magnitude_c = in_q31;
        end

        if (magnitude_c[31]) begin
            highest_c = 6'd31;
        end else if (magnitude_c[30]) begin
            highest_c = 6'd30;
        end else if (magnitude_c[29]) begin
            highest_c = 6'd29;
        end else if (magnitude_c[28]) begin
            highest_c = 6'd28;
        end else if (magnitude_c[27]) begin
            highest_c = 6'd27;
        end else if (magnitude_c[26]) begin
            highest_c = 6'd26;
        end else if (magnitude_c[25]) begin
            highest_c = 6'd25;
        end else if (magnitude_c[24]) begin
            highest_c = 6'd24;
        end else if (magnitude_c[23]) begin
            highest_c = 6'd23;
        end else if (magnitude_c[22]) begin
            highest_c = 6'd22;
        end else if (magnitude_c[21]) begin
            highest_c = 6'd21;
        end else if (magnitude_c[20]) begin
            highest_c = 6'd20;
        end else if (magnitude_c[19]) begin
            highest_c = 6'd19;
        end else if (magnitude_c[18]) begin
            highest_c = 6'd18;
        end else if (magnitude_c[17]) begin
            highest_c = 6'd17;
        end else if (magnitude_c[16]) begin
            highest_c = 6'd16;
        end else if (magnitude_c[15]) begin
            highest_c = 6'd15;
        end else if (magnitude_c[14]) begin
            highest_c = 6'd14;
        end else if (magnitude_c[13]) begin
            highest_c = 6'd13;
        end else if (magnitude_c[12]) begin
            highest_c = 6'd12;
        end else if (magnitude_c[11]) begin
            highest_c = 6'd11;
        end else if (magnitude_c[10]) begin
            highest_c = 6'd10;
        end else if (magnitude_c[9]) begin
            highest_c = 6'd9;
        end else if (magnitude_c[8]) begin
            highest_c = 6'd8;
        end else if (magnitude_c[7]) begin
            highest_c = 6'd7;
        end else if (magnitude_c[6]) begin
            highest_c = 6'd6;
        end else if (magnitude_c[5]) begin
            highest_c = 6'd5;
        end else if (magnitude_c[4]) begin
            highest_c = 6'd4;
        end else if (magnitude_c[3]) begin
            highest_c = 6'd3;
        end else if (magnitude_c[2]) begin
            highest_c = 6'd2;
        end else if (magnitude_c[1]) begin
            highest_c = 6'd1;
        end else begin
            highest_c = 6'd0;
        end

        exponent_c = highest_c + 8'd96;

        if (highest_c <= 6'd23) begin
            shifted_c  = magnitude_c << (6'd23 - highest_c);
            fraction_c = shifted_c[22:0];
        end else begin
            round_shift_c = highest_c - 6'd23;
            shifted_c = magnitude_c >> round_shift_c;
            remainder_c = magnitude_c & ((32'd1 << round_shift_c) - 32'd1);
            halfway_c = 32'd1 << (round_shift_c - 6'd1);
            if ((remainder_c > halfway_c) ||
                ((remainder_c == halfway_c) && shifted_c[0]))
                shifted_c = shifted_c + 32'd1;
            if (shifted_c[24]) begin
                shifted_c = shifted_c >> 1;
                exponent_c = exponent_c + 8'd1;
            end
            fraction_c = shifted_c[22:0];
        end

        out_fp32_c = {sign_c, exponent_c, fraction_c};
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy     <= 1'b0;
        out_fp32 <= 32'h00000000;
    end else if (input_accepted) begin
        busy     <= 1'b1;
        out_fp32 <= out_fp32_c;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
