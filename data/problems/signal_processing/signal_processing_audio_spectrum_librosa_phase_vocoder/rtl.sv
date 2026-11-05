module signal_processing_audio_spectrum_librosa_phase_vocoder (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [15:0] in_c0_real,
    input  logic [15:0] in_c0_imag,
    input  logic [15:0] in_c1_real,
    input  logic [15:0] in_c1_imag,
    input  logic [63:0] in_phase_acc,
    input  logic [16:0] in_alpha,
    input  logic [63:0] in_phi_advance,
    output logic out_valid,
    input  logic out_ready,
    output logic [16:0] out_real,
    output logic [16:0] out_imag,
    output logic [63:0] out_phase_acc
);

function automatic logic signed [63:0] atan_step(input integer i);
    case (i)
        0: atan_step = 64'sd536870912;
        1: atan_step = 64'sd316933406;
        2: atan_step = 64'sd167458907;
        3: atan_step = 64'sd85004756;
        4: atan_step = 64'sd42667331;
        5: atan_step = 64'sd21354465;
        6: atan_step = 64'sd10679838;
        7: atan_step = 64'sd5340245;
        8: atan_step = 64'sd2670163;
        9: atan_step = 64'sd1335087;
        10: atan_step = 64'sd667544;
        11: atan_step = 64'sd333772;
        12: atan_step = 64'sd166886;
        13: atan_step = 64'sd83443;
        14: atan_step = 64'sd41722;
        15: atan_step = 64'sd20861;
        16: atan_step = 64'sd10430;
        17: atan_step = 64'sd5215;
        18: atan_step = 64'sd2608;
        19: atan_step = 64'sd1304;
        20: atan_step = 64'sd652;
        21: atan_step = 64'sd326;
        22: atan_step = 64'sd163;
        23: atan_step = 64'sd81;
        24: atan_step = 64'sd41;
        25: atan_step = 64'sd20;
        26: atan_step = 64'sd10;
        27: atan_step = 64'sd5;
        28: atan_step = 64'sd3;
        29: atan_step = 64'sd1;
        default: atan_step = 0;
    endcase
endfunction

function automatic logic [31:0] magnitude(input logic signed [15:0] xr, yi);
    logic signed [63:0] x, y;
    logic [63:0] square, trial, remainder;
    logic [31:0] root;
    x = xr;
    y = yi;
    square = (x*x + y*y) << 32;
    root = 0;
    remainder = 0;
    for (integer bit_index = 31; bit_index >= 0; bit_index = bit_index - 1) begin
        remainder = (remainder << 2) | ((square >> (2*bit_index)) & 64'd3);
        trial = ({32'b0, root} << 2) | 64'd1;
        root = root << 1;
        if (remainder >= trial) begin
            remainder = remainder - trial;
            root = root | 32'd1;
        end
    end
    magnitude = root;
endfunction

function automatic logic signed [63:0] angle(input logic signed [15:0] xr, yi);
    logic signed [63:0] x, y, z, old_x;
    x = xr;
    y = yi;
    z = 0;
    old_x = 0;
    if (y == 0) z = x >= 0 ? 64'sd0 : 64'sd2147483648;
    else if (x == 0) z = y > 0 ? 64'sd1073741824 : -64'sd1073741824;
    else begin
        if (x < 0) begin
            z = y > 0 ? 64'sd2147483648 : -64'sd2147483648;
            x = -x;
            y = -y;
        end
        x = x <<< 24;
        y = y <<< 24;
        for (integer i = 0; i < 30; i = i + 1) begin
            old_x = x;
            if (y >= 0) begin
                x = x + (y >>> i);
                y = y - (old_x >>> i);
                z = z + atan_step(i);
            end else begin
                x = x - (y >>> i);
                y = y + (old_x >>> i);
                z = z - atan_step(i);
            end
        end
    end
    angle = z;
endfunction

logic [16:0] result_real, result_imag;
logic [63:0] result_phase;
assign in_ready = !out_valid || out_ready;

always_comb begin : bin_step
    logic [16:0] alpha;
    logic [63:0] mag0, mag1, mag;
    logic signed [63:0] x, y, z, old_x, phase, advance;
    logic signed [65:0] delta, turns, fraction, residual, next_phase;
    alpha = in_alpha > 17'd65536 ? 17'd65536 : in_alpha;
    mag0 = {32'b0, magnitude($signed(in_c0_real), $signed(in_c0_imag))};
    mag1 = {32'b0, magnitude($signed(in_c1_real), $signed(in_c1_imag))};
    mag = ((64'd65536-alpha)*mag0 + alpha*mag1) >> 16;
    x = $signed((mag*64'd652032874) >> 30);
    y = 0;
    z = $signed(in_phase_acc[31:0]);
    phase = $signed(in_phase_acc);
    advance = $signed(in_phi_advance);
    old_x = 0;
    if (z > 64'sd1073741824) begin
        x = -x;
        z = z - 64'sd2147483648;
    end else if (z < -64'sd1073741824) begin
        x = -x;
        z = z + 64'sd2147483648;
    end
    for (integer i = 0; i < 30; i = i + 1) begin
        old_x = x;
        if (z >= 0) begin
            x = x - (y >>> i);
            y = y + (old_x >>> i);
            z = z - atan_step(i);
        end else begin
            x = x + (y >>> i);
            y = y - (old_x >>> i);
            z = z + atan_step(i);
        end
    end
    result_real = 17'((x + 64'sd32768) >>> 16);
    result_imag = 17'((y + 64'sd32768) >>> 16);
    delta = angle($signed(in_c1_real), $signed(in_c1_imag)) -
            angle($signed(in_c0_real), $signed(in_c0_imag)) -
            $signed({{2{advance[63]}}, advance});
    turns = delta >>> 32;
    fraction = delta - (turns <<< 32);
    if (fraction > 66'sd2147483648 ||
        (fraction == 66'sd2147483648 && turns[0]))
        turns = turns + 66'sd1;
    residual = delta - (turns <<< 32);
    next_phase = $signed({{2{phase[63]}}, phase}) +
                 $signed({{2{advance[63]}}, advance}) + residual;
    result_phase = next_phase[63:0];
end

always_ff @(posedge clk) begin
    if (rst) begin
        out_valid <= 1'b0;
        out_real <= '0;
        out_imag <= '0;
        out_phase_acc <= '0;
    end else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin
            out_real <= result_real;
            out_imag <= result_imag;
            out_phase_acc <= result_phase;
        end
    end
end
endmodule
