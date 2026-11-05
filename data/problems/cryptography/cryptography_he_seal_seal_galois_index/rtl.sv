module cryptography_he_seal_seal_galois_index (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [4:0]  in_coeff_count_power,
    input  logic [31:0] in_step,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_galois_elt,
    output logic        out_valid_step
);

logic busy;
wire  input_accepted;
wire  output_consumed;

logic [31:0] result_galois_elt;
logic        result_valid_step;

logic [31:0] step_mag;
logic        step_is_zero;
logic        step_is_neg;
logic [31:0] n_val;
logic [31:0] half_n;
logic [31:0] m_val;
logic [31:0] mask_val;
logic        step_valid;
logic [31:0] exponent_val;
logic [31:0] galois_elt_calc;
logic [31:0] power_calc;

integer i;

assign input_accepted  = in_valid & in_ready;
assign output_consumed = out_valid & out_ready;

assign in_ready  = ~busy | output_consumed;
assign out_valid = busy;

always_comb begin
    step_is_neg  = in_step[31];
    step_is_zero = (in_step == 32'd0);

    if (step_is_neg) begin
        if (in_step == 32'h8000_0000) begin
            step_mag = 32'h8000_0000;
        end else begin
            step_mag = (~in_step) + 32'd1;
        end
    end else begin
        step_mag = in_step;
    end

    n_val = 32'd1 << in_coeff_count_power;
    half_n = n_val >> 1;
    m_val = n_val << 1;
    mask_val = m_val - 32'd1;

    if (in_coeff_count_power < 5'd1) begin
        step_valid = 1'b0;
    end else if (step_mag >= half_n) begin
        step_valid = 1'b0;
    end else begin
        step_valid = 1'b1;
    end

    if (step_is_neg) begin
        exponent_val = half_n - step_mag;
    end else begin
        exponent_val = step_mag;
    end

    galois_elt_calc = 32'd1;
    power_calc = 32'd3 & mask_val;
    for (i = 0; i < 32; i = i + 1) begin
        if (exponent_val[i]) begin
            galois_elt_calc = (galois_elt_calc * power_calc) & mask_val;
        end
        power_calc = (power_calc * power_calc) & mask_val;
    end

    if (!step_valid) begin
        result_valid_step = 1'b0;
        result_galois_elt = 32'd0;
    end else if (step_is_zero) begin
        result_valid_step = 1'b1;
        result_galois_elt = m_val - 32'd1;
    end else begin
        result_valid_step = 1'b1;
        result_galois_elt = galois_elt_calc;
    end
end

always_ff @(posedge clk) begin
    if (rst) begin
        busy           <= 1'b0;
        out_galois_elt <= 32'd0;
        out_valid_step <= 1'b0;
    end else if (input_accepted) begin
        busy           <= 1'b1;
        out_galois_elt <= result_galois_elt;
        out_valid_step <= result_valid_step;
    end else if (output_consumed) begin
        busy <= 1'b0;
    end
end

endmodule
