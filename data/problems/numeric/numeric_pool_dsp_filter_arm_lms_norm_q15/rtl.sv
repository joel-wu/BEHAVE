module q15_normalized_lms_filter_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_sample,
    input  logic [15:0]  in_ref,
    input  logic [15:0]  in_mu,
    input  logic  [4:0]  in_postShift,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_sample,
    output logic [15:0]  out_error
);

logic busy;
wire  output_consumed;
wire  input_accepted;

logic signed [15:0] coeffs [0:7];
logic signed [15:0] state  [0:6];
logic signed [15:0] energy;
logic signed [15:0] x0;

logic signed [15:0] calc_y;
logic signed [15:0] calc_e;
logic signed [15:0] energy_new_comb;
logic signed [15:0] coeffs_next_comb [0:7];
logic signed [15:0] state_next_comb  [0:6];
logic signed [15:0] x0_next_comb;

integer i;

function automatic logic signed [15:0] sat16(input logic signed [31:0] value);
    begin
        sat16 = value[15:0];
        if (value > 32'sd32767) begin
            sat16 = 16'sd32767;
        end else if (value < -32'sd32768) begin
            sat16 = -16'sd32768;
        end
    end
endfunction

function automatic logic signed [15:0] sat16_from64(input logic signed [63:0] value);
    begin
        sat16_from64 = value[15:0];
        if (value > 64'sd32767) begin
            sat16_from64 = 16'sd32767;
        end else if (value < -64'sd32768) begin
            sat16_from64 = -16'sd32768;
        end
    end
endfunction

function automatic logic signed [15:0] normalized_weight(
    input logic signed [15:0] error_mu,
    input logic signed [15:0] denominator
);
    logic [15:0] normalized;
    logic signed [15:0] estimate;
    logic signed [31:0] product, residual;
    integer shift, step;
    begin
        normalized_weight = 16'sd0;
        normalized = 16'd0;
        estimate = 16'sd0;
        product = 32'sd0;
        residual = 32'sd0;
        shift = 0;
        if (denominator > 0) begin
            normalized = denominator;
            for (step = 0; step < 14; step = step + 1) begin
                if (normalized < 16'h4000) begin
                    normalized = normalized << 1;
                    shift = shift + 1;
                end
            end
            case (normalized[13:8])
                6'd0: estimate = 16'h7F03;
                6'd1: estimate = 16'h7D13;
                6'd2: estimate = 16'h7B31;
                6'd3: estimate = 16'h795E;
                6'd4: estimate = 16'h7798;
                6'd5: estimate = 16'h75E0;
                6'd6: estimate = 16'h7434;
                6'd7: estimate = 16'h7294;
                6'd8: estimate = 16'h70FF;
                6'd9: estimate = 16'h6F76;
                6'd10: estimate = 16'h6DF6;
                6'd11: estimate = 16'h6C82;
                6'd12: estimate = 16'h6B16;
                6'd13: estimate = 16'h69B5;
                6'd14: estimate = 16'h685C;
                6'd15: estimate = 16'h670C;
                6'd16: estimate = 16'h65C4;
                6'd17: estimate = 16'h6484;
                6'd18: estimate = 16'h634C;
                6'd19: estimate = 16'h621C;
                6'd20: estimate = 16'h60F3;
                6'd21: estimate = 16'h5FD0;
                6'd22: estimate = 16'h5EB5;
                6'd23: estimate = 16'h5DA0;
                6'd24: estimate = 16'h5C91;
                6'd25: estimate = 16'h5B88;
                6'd26: estimate = 16'h5A85;
                6'd27: estimate = 16'h5988;
                6'd28: estimate = 16'h5890;
                6'd29: estimate = 16'h579E;
                6'd30: estimate = 16'h56B0;
                6'd31: estimate = 16'h55C8;
                6'd32: estimate = 16'h54E4;
                6'd33: estimate = 16'h5405;
                6'd34: estimate = 16'h532B;
                6'd35: estimate = 16'h5255;
                6'd36: estimate = 16'h5183;
                6'd37: estimate = 16'h50B6;
                6'd38: estimate = 16'h4FEC;
                6'd39: estimate = 16'h4F26;
                6'd40: estimate = 16'h4E64;
                6'd41: estimate = 16'h4DA6;
                6'd42: estimate = 16'h4CEC;
                6'd43: estimate = 16'h4C34;
                6'd44: estimate = 16'h4B81;
                6'd45: estimate = 16'h4AD0;
                6'd46: estimate = 16'h4A23;
                6'd47: estimate = 16'h4978;
                6'd48: estimate = 16'h48D1;
                6'd49: estimate = 16'h482D;
                6'd50: estimate = 16'h478C;
                6'd51: estimate = 16'h46ED;
                6'd52: estimate = 16'h4651;
                6'd53: estimate = 16'h45B8;
                6'd54: estimate = 16'h4521;
                6'd55: estimate = 16'h448D;
                6'd56: estimate = 16'h43FC;
                6'd57: estimate = 16'h436C;
                6'd58: estimate = 16'h42DF;
                6'd59: estimate = 16'h4255;
                6'd60: estimate = 16'h41CC;
                6'd61: estimate = 16'h4146;
                6'd62: estimate = 16'h40C2;
                6'd63: estimate = 16'h4040;
            endcase
            for (step = 0; step < 2; step = step + 1) begin
                product = $signed(normalized) * estimate;
                residual = 32'sd32767 - (product >>> 15);
                product = estimate * residual;
                estimate = product >>> 14;
            end
            product = error_mu * estimate;
            normalized_weight = sat16(product >>> (14 - shift));
        end
    end
endfunction

always_comb begin
    logic signed [15:0] x [0:7];
    logic signed [31:0] prod_old;
    logic signed [31:0] prod_new;
    logic signed [31:0] energy_tmp32;
    logic signed [63:0] mac64;
    logic signed [63:0] shifted64;
    logic signed [31:0] errorXmu32;
    logic signed [15:0] error_mu16;
    logic signed [15:0] denominator16;
    logic signed [15:0] w16;
    logic signed [31:0] coef_tmp32;
    logic signed [31:0] ref_minus_y32;
    logic [5:0]         lShift_u;
    integer j;

    calc_y = 16'sd0;
    calc_e = 16'sd0;
    energy_new_comb = 16'sd0;
    x0_next_comb = 16'sd0;
    for (j = 0; j < 8; j = j + 1) begin
        coeffs_next_comb[j] = 16'sd0;
        x[j] = 16'sd0;
    end
    for (j = 0; j < 7; j = j + 1) begin
        state_next_comb[j] = 16'sd0;
    end

    x[0] = state[0];
    x[1] = state[1];
    x[2] = state[2];
    x[3] = state[3];
    x[4] = state[4];
    x[5] = state[5];
    x[6] = state[6];
    x[7] = $signed(in_sample);

    prod_old = x0 * x0;
    prod_new = $signed(in_sample) * $signed(in_sample);
    energy_tmp32 = $signed(energy) - (prod_old >>> 15) + (prod_new >>> 15);
    energy_new_comb = sat16(energy_tmp32);

    mac64 = 64'sd0;
    mac64 = mac64 + (x[0] * coeffs[0]);
    mac64 = mac64 + (x[1] * coeffs[1]);
    mac64 = mac64 + (x[2] * coeffs[2]);
    mac64 = mac64 + (x[3] * coeffs[3]);
    mac64 = mac64 + (x[4] * coeffs[4]);
    mac64 = mac64 + (x[5] * coeffs[5]);
    mac64 = mac64 + (x[6] * coeffs[6]);
    mac64 = mac64 + (x[7] * coeffs[7]);

    lShift_u = 6'(15 - {1'b0, in_postShift});
    shifted64 = 64'sd0;
    if (lShift_u[5] == 1'b1) begin
        shifted64 = mac64 <<< (6'd0 - lShift_u);
    end else begin
        shifted64 = mac64 >>> lShift_u;
    end
    calc_y = sat16_from64(shifted64);

    ref_minus_y32 = $signed(in_ref) - calc_y;
    calc_e = ref_minus_y32[15:0];

    errorXmu32 = (calc_e * $signed(in_mu)) >>> 15;
    error_mu16 = errorXmu32[15:0];
    denominator16 = $signed(energy_new_comb) + 32'sd5;
    w16 = normalized_weight(error_mu16, denominator16);

    for (j = 0; j < 8; j = j + 1) begin
        coef_tmp32 = coeffs[j] + ((w16 * x[j]) >>> 15);
        coeffs_next_comb[j] = sat16(coef_tmp32);
    end

    x0_next_comb = state[0];
    state_next_comb[0] = state[1];
    state_next_comb[1] = state[2];
    state_next_comb[2] = state[3];
    state_next_comb[3] = state[4];
    state_next_comb[4] = state[5];
    state_next_comb[5] = state[6];
    state_next_comb[6] = $signed(in_sample);
end

assign out_valid = busy;
assign output_consumed = out_valid & out_ready;
assign in_ready = ~busy | output_consumed;
assign input_accepted = in_valid & in_ready;

always_ff @(posedge clk) begin
    if (rst) begin
        busy <= 1'b0;
        out_sample <= 16'h0000;
        out_error <= 16'h0000;
        energy <= 16'sd0;
        x0 <= 16'sd0;
        for (i = 0; i < 8; i = i + 1) begin
            coeffs[i] <= 16'sd0;
        end
        for (i = 0; i < 7; i = i + 1) begin
            state[i] <= 16'sd0;
        end
    end else if (input_accepted) begin
        busy <= 1'b1;
        out_sample <= calc_y[15:0];
        out_error <= calc_e[15:0];
        energy <= energy_new_comb;
        x0 <= x0_next_comb;
        for (i = 0; i < 8; i = i + 1) begin
            coeffs[i] <= coeffs_next_comb[i];
        end
        for (i = 0; i < 7; i = i + 1) begin
            state[i] <= state_next_comb[i];
        end
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
