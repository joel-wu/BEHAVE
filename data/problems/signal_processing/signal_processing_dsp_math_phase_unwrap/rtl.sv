module signal_processing_dsp_math_phase_unwrap (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic [31:0] in_n,
    input  logic [63:0] in_lev,
    input  logic [63:0] in_mar,
    input  logic [63:0] in_phi0,
    input  logic [63:0] in_phi1,
    input  logic [63:0] in_phi2,
    input  logic [63:0] in_phi3,
    input  logic [63:0] in_phi4,
    input  logic [63:0] in_phi5,
    input  logic [63:0] in_phi6,
    input  logic [63:0] in_phi7,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [31:0] out_status,
    output logic [127:0] out_phi0,
    output logic [127:0] out_phi1,
    output logic [127:0] out_phi2,
    output logic [127:0] out_phi3,
    output logic [127:0] out_phi4,
    output logic [127:0] out_phi5,
    output logic [127:0] out_phi6,
    output logic [127:0] out_phi7
);

    logic occupied;
    logic signed [127:0] phases[0:7], result[0:7];
    logic signed [127:0] lev, margin, threshold, period, correction, delta, excess, count;
    logic [31:0] status;
    assign in_ready = !occupied || out_ready;
    assign out_valid = occupied;
    always_comb begin
        phases[0] = $signed(in_phi0);
        phases[1] = $signed(in_phi1);
        phases[2] = $signed(in_phi2);
        phases[3] = $signed(in_phi3);
        phases[4] = $signed(in_phi4);
        phases[5] = $signed(in_phi5);
        phases[6] = $signed(in_phi6);
        phases[7] = $signed(in_phi7);
        for (int i=0;i<8;i++) result[i] = phases[i];
        lev = $signed(in_lev); margin = $signed(in_mar);
        status = 0; threshold = 0; period = 0; correction = 0;
        delta = 0; excess = 0; count = 0;
        if (in_n < 1 || in_n > 8) status = 1;
        else if (lev <= 0 || margin < 32768) status = 2;
        else begin
            threshold = margin * lev;
            period = lev <<< 16;
            for (int i=1;i<8;i++) begin
                if (i < in_n) begin
                    delta = phases[i] - phases[i-1];
                    excess = ((delta < 0 ? -delta : delta) <<< 16) - threshold;
                    if (excess > 0) begin
                        count = (excess + period - 1) / period;
                        correction += (delta > 0 ? -count : count) * lev;
                    end
                    result[i] = phases[i] + correction;
                end
            end
        end
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            occupied <= 0; out_status <= 0;
            out_phi0 <= 0;
            out_phi1 <= 0;
            out_phi2 <= 0;
            out_phi3 <= 0;
            out_phi4 <= 0;
            out_phi5 <= 0;
            out_phi6 <= 0;
            out_phi7 <= 0;
        end else if (in_valid && in_ready) begin
            occupied <= 1; out_status <= status;
            out_phi0 <= result[0];
            out_phi1 <= result[1];
            out_phi2 <= result[2];
            out_phi3 <= result[3];
            out_phi4 <= result[4];
            out_phi5 <= result[5];
            out_phi6 <= result[6];
            out_phi7 <= result[7];
        end else if (out_ready) occupied <= 0;
    end
endmodule
