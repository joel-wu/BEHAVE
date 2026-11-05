// SPDX-License-Identifier: Apache-2.0
// Fixed-point adaptation of FilterPy GHFilter.update; see source.json.
module AlphaBetaTrackerQ16 (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_measurement,
    input  logic [15:0] in_alpha,
    input  logic [15:0] in_beta,
    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_position,
    output logic [31:0] out_velocity
);
    logic signed [31:0] position, velocity;
    logic signed [31:0] next_position, next_velocity;
    logic signed [63:0] measurement_wide, position_wide, velocity_wide;
    logic signed [63:0] alpha_wide, beta_wide;
    logic signed [63:0] predicted, residual, position_raw, velocity_raw;

    always_comb begin
        measurement_wide = {{32{in_measurement[31]}}, in_measurement};
        position_wide = {{32{position[31]}}, position};
        velocity_wide = {{32{velocity[31]}}, velocity};
        alpha_wide = {48'b0, in_alpha};
        beta_wide = {48'b0, in_beta};
        predicted = position_wide + velocity_wide;
        residual = measurement_wide - predicted;
        position_raw = predicted + ((alpha_wide * residual) >>> 15);
        velocity_raw = velocity_wide + ((beta_wide * residual) >>> 15);
        if (position_raw > 64'sd2147483647)
            next_position = 32'sh7fffffff;
        else if (position_raw < -64'sd2147483648)
            next_position = 32'sh80000000;
        else
            next_position = position_raw[31:0];
        if (velocity_raw > 64'sd2147483647)
            next_velocity = 32'sh7fffffff;
        else if (velocity_raw < -64'sd2147483648)
            next_velocity = 32'sh80000000;
        else
            next_velocity = velocity_raw[31:0];
    end

    assign in_ready = !rst && (!out_valid || out_ready);

    always_ff @(posedge clk) begin
        if (rst) begin
            position <= '0;
            velocity <= '0;
            out_position <= '0;
            out_velocity <= '0;
            out_valid <= 1'b0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                position <= next_position;
                velocity <= next_velocity;
                out_position <= next_position;
                out_velocity <= next_velocity;
            end
        end
    end
endmodule
