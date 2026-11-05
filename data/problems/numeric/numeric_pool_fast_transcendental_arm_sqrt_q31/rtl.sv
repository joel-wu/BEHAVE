module q31_sqrt (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_value,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_root,
    output logic        out_status
);

logic busy;
wire  input_accepted;
wire  output_consumed;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

function automatic logic [5:0] clz32(input logic [31:0] v);
    integer i;
    begin
        clz32 = 6'd32;
        for (i = 31; i >= 0; i = i - 1) begin
            if (v[i]) begin
                clz32 = 6'(31 - i);
                i = -1;
            end
        end
    end
endfunction

function automatic logic signed [31:0] lut_value(input logic [4:0] idx);
    begin
        case (idx)
            5'd0: lut_value = 32'sd536870912;
            5'd1: lut_value = 32'sd506166750;
            5'd2: lut_value = 32'sd480191942;
            5'd3: lut_value = 32'sd457845052;
            5'd4: lut_value = 32'sd438353264;
            5'd5: lut_value = 32'sd421156193;
            5'd6: lut_value = 32'sd405836263;
            5'd7: lut_value = 32'sd392075079;
            5'd8: lut_value = 32'sd379625062;
            5'd9: lut_value = 32'sd368290407;
            5'd10: lut_value = 32'sd357913941;
            5'd11: lut_value = 32'sd348367849;
            5'd12: lut_value = 32'sd339546978;
            5'd13: lut_value = 32'sd331363921;
            5'd14: lut_value = 32'sd323745341;
            5'd15: lut_value = 32'sd316629190;
            5'd16: lut_value = 32'sd309962566;
            5'd17: lut_value = 32'sd303700050;
            5'd18: lut_value = 32'sd297802400;
            5'd19: lut_value = 32'sd292235509;
            5'd20: lut_value = 32'sd286969573;
            5'd21: lut_value = 32'sd281978417;
            5'd22: lut_value = 32'sd277238947;
            5'd23: lut_value = 32'sd272730696;
            default: lut_value = 32'sd0;
        endcase
    end
endfunction

function automatic logic [31:0] calc_sqrt_q31(input logic [31:0] in_bits);
    logic signed [31:0] number;
    logic [31:0] number_u;
    logic [5:0]  clz_v;
    logic signed [31:0] signBits1;
    logic signed [31:0] norm_shift;
    logic signed [31:0] result_shift;
    logic [4:0]  lut_idx;
    logic signed [31:0] var1;
    logic signed [31:0] temp;
    logic signed [63:0] prod64;
    begin
        calc_sqrt_q31 = 32'h00000000;
        number = $signed(in_bits);

        if (number > 32'sd0) begin
            number_u  = in_bits;
            clz_v     = clz32(number_u);
            signBits1 = $signed({26'd0, clz_v}) - 32'sd1;

            if (signBits1[0] == 1'b0)
                norm_shift = signBits1;
            else
                norm_shift = signBits1 - 32'sd1;

            number = number <<< norm_shift[4:0];

            lut_idx = number[30:26] - 5'd8;
            var1 = lut_value(lut_idx);

            prod64 = $signed(var1) * $signed(var1);
            temp = $signed(prod64 >>> 28);
            prod64 = $signed(number) * $signed(temp);
            temp = $signed(prod64 >>> 31);
            temp = 32'sh30000000 - temp;
            prod64 = $signed(var1) * $signed(temp);
            var1 = $signed(prod64 >>> 29);

            prod64 = $signed(var1) * $signed(var1);
            temp = $signed(prod64 >>> 28);
            prod64 = $signed(number) * $signed(temp);
            temp = $signed(prod64 >>> 31);
            temp = 32'sh30000000 - temp;
            prod64 = $signed(var1) * $signed(temp);
            var1 = $signed(prod64 >>> 29);

            prod64 = $signed(var1) * $signed(var1);
            temp = $signed(prod64 >>> 28);
            prod64 = $signed(number) * $signed(temp);
            temp = $signed(prod64 >>> 31);
            temp = 32'sh30000000 - temp;
            prod64 = $signed(var1) * $signed(temp);
            var1 = $signed(prod64 >>> 29);

            prod64 = $signed(number) * $signed(var1);
            var1 = $signed(prod64 >>> 28);

            if (signBits1[0] == 1'b0)
                result_shift = signBits1 >>> 1;
            else
                result_shift = (signBits1 - 32'sd1) >>> 1;

            var1 = var1 >>> result_shift[4:0];
            calc_sqrt_q31 = var1;
        end
    end
endfunction

always_ff @(posedge clk) begin
    if (rst) begin
        busy       <= 1'b0;
        out_root   <= 32'h00000000;
        out_status <= 1'b0;
    end else if (input_accepted) begin
        if ($signed(in_value) < 32'sd0) begin
            out_root   <= 32'h00000000;
            out_status <= 1'b1;
        end else if ($signed(in_value) == 32'sd0) begin
            out_root   <= 32'h00000000;
            out_status <= 1'b0;
        end else begin
            out_root   <= calc_sqrt_q31(in_value);
            out_status <= 1'b0;
        end
        busy <= 1'b1;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
