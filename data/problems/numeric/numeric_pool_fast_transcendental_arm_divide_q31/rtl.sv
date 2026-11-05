module q31_normalizing_divider (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [31:0]  in_numerator,
    input  logic [31:0]  in_denominator,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [31:0]  out_quotient,
    output logic [15:0]  out_shift,
    output logic         out_status_naninf
);

logic busy;

wire input_accepted;
wire output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [31:0] abs_q31_sat32(input logic [31:0] x);
    begin
        if (x == 32'h80000000) begin
            abs_q31_sat32 = 32'h7fffffff;
        end else if (x[31]) begin
            abs_q31_sat32 = ~x + 32'd1;
        end else begin
            abs_q31_sat32 = x;
        end
    end
endfunction

function automatic logic [5:0] clz32(input logic [31:0] v);
    logic [15:0] upper16;
    logic [7:0]  upper8;
    logic [3:0]  upper4;
    logic [1:0]  upper2;
    begin
        upper16 = v[31:16];
        upper8  = 8'd0;
        upper4  = 4'd0;
        upper2  = 2'd0;

        if (upper16 != 16'd0) begin
            upper8 = upper16[15:8];
            if (upper8 != 8'd0) begin
                upper4 = upper8[7:4];
                if (upper4 != 4'd0) begin
                    upper2 = upper4[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd0;
                        else           clz32 = 6'd1;
                    end else begin
                        if (upper4[1]) clz32 = 6'd2;
                        else           clz32 = 6'd3;
                    end
                end else begin
                    upper2 = upper8[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd4;
                        else           clz32 = 6'd5;
                    end else begin
                        if (upper8[1]) clz32 = 6'd6;
                        else           clz32 = 6'd7;
                    end
                end
            end else begin
                upper4 = upper16[7:4];
                if (upper4 != 4'd0) begin
                    upper2 = upper4[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd8;
                        else           clz32 = 6'd9;
                    end else begin
                        if (upper4[1]) clz32 = 6'd10;
                        else           clz32 = 6'd11;
                    end
                end else begin
                    upper2 = upper16[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd12;
                        else           clz32 = 6'd13;
                    end else begin
                        if (upper16[1]) clz32 = 6'd14;
                        else           clz32 = 6'd15;
                    end
                end
            end
        end else begin
            upper8 = v[15:8];
            if (upper8 != 8'd0) begin
                upper4 = upper8[7:4];
                if (upper4 != 4'd0) begin
                    upper2 = upper4[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd16;
                        else           clz32 = 6'd17;
                    end else begin
                        if (upper4[1]) clz32 = 6'd18;
                        else           clz32 = 6'd19;
                    end
                end else begin
                    upper2 = upper8[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd20;
                        else           clz32 = 6'd21;
                    end else begin
                        if (upper8[1]) clz32 = 6'd22;
                        else           clz32 = 6'd23;
                    end
                end
            end else begin
                upper4 = v[7:4];
                if (upper4 != 4'd0) begin
                    upper2 = upper4[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd24;
                        else           clz32 = 6'd25;
                    end else begin
                        if (upper4[1]) clz32 = 6'd26;
                        else           clz32 = 6'd27;
                    end
                end else begin
                    upper2 = v[3:2];
                    if (upper2 != 2'd0) begin
                        if (upper2[1]) clz32 = 6'd28;
                        else           clz32 = 6'd29;
                    end else if (v[1]) begin
                        clz32 = 6'd30;
                    end else if (v[0]) begin
                        clz32 = 6'd31;
                    end else begin
                        clz32 = 6'd32;
                    end
                end
            end
        end
    end
endfunction

logic        sign_c;
logic [31:0] abs_num_c;
logic [31:0] abs_den_c;
logic [63:0] temp_mag_c;
logic [32:0] high_c;
logic [31:0] high32_c;
logic [5:0]  clz_high_c;
logic [5:0]  shift_norm_c;
logic [63:0] temp_norm_c;
logic [63:0] temp_signed_c;

always_comb begin
    sign_c              = in_numerator[31] ^ in_denominator[31];
    abs_num_c           = abs_q31_sat32(in_numerator);
    abs_den_c           = abs_q31_sat32(in_denominator);
    temp_mag_c          = 64'd0;
    high_c              = 33'd0;
    high32_c            = 32'd0;
    clz_high_c          = 6'd32;
    shift_norm_c        = 6'd0;
    temp_norm_c         = 64'd0;
    temp_signed_c       = 64'd0;

    if (in_denominator != 32'd0) begin
        temp_mag_c   = ({32'd0, abs_num_c} << 31) / abs_den_c;
        high_c       = temp_mag_c[63:31];
        high32_c     = high_c[31:0];
        clz_high_c   = clz32(high32_c);
        shift_norm_c = 6'd32 - clz_high_c;

        if (shift_norm_c > 6'd0) begin
            temp_norm_c = temp_mag_c >> shift_norm_c;
        end else begin
            temp_norm_c = temp_mag_c;
        end

        if (sign_c) begin
            temp_signed_c = ~temp_norm_c + 64'd1;
        end else begin
            temp_signed_c = temp_norm_c;
        end
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy              <= 1'b0;
        out_quotient      <= 32'h0000_0000;
        out_shift         <= 16'h0000;
        out_status_naninf <= 1'b0;
    end else if (input_accepted) begin
        busy <= 1'b1;
        if (in_denominator == 32'd0) begin
            out_shift         <= 16'h0000;
            out_status_naninf <= 1'b1;
            if (sign_c) begin
                out_quotient <= 32'h8000_0000;
            end else begin
                out_quotient <= 32'h7FFF_FFFF;
            end
        end else begin
            out_quotient      <= temp_signed_c[31:0];
            out_shift         <= {10'd0, shift_norm_c};
            out_status_naninf <= 1'b0;
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
