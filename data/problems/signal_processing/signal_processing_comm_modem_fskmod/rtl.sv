module signal_processing_comm_modem_fskmod #(
    parameter int M_BITS = 1,
    parameter int K = 2,
    parameter int PHASE_W = 32,
    parameter int OUT_W = 16
) (
    input  logic                    clk,
    input  logic                    rst,

    input  logic                    in_valid,
    output logic                    in_ready,
    input  logic [M_BITS-1:0]       in_symbol,

    output logic                    out_valid,
    input  logic                    out_ready,
    output logic [OUT_W-1:0]        out_i,
    output logic [OUT_W-1:0]        out_q
);

    localparam int PTR_W = K <= 1 ? 1 : $clog2(K);
    localparam int M = 1 << M_BITS;
    logic busy;
    logic [PTR_W-1:0] beat;
    logic [PHASE_W-1:0] phase_acc, increment;
    wire transfer = out_valid && out_ready;
    wire last_sample = transfer && beat == PTR_W'(K-1);
    wire [3:0] index = phase_acc[PHASE_W-1 -: 4];

    assign out_valid = busy;
    assign in_ready = !busy || last_sample;

    function automatic logic [PHASE_W-1:0] phase_step(input logic [M_BITS-1:0] symbol);
        logic signed [63:0] numerator, magnitude, rounded_step;
        begin
            numerator = (64'($unsigned(symbol)) * 64'sd2 - 64'(M-1))
                        * (64'sd1 << (PHASE_W-2));
            magnitude = numerator < 0 ? -numerator : numerator;
            rounded_step = (magnitude + 64'((M-1)/2)) / 64'(M-1);
            if (numerator < 0)
                rounded_step = -rounded_step;
            phase_step = PHASE_W'(rounded_step);
        end
    endfunction

    function automatic logic [OUT_W-1:0] sample(input logic [3:0] address);
        logic signed [63:0] coefficient, magnitude, rounded_value, limit_value;
        begin
            case (address)
                4'd0: coefficient = 64'sd2147483648;
                4'd1: coefficient = 64'sd1984016189;
                4'd2: coefficient = 64'sd1518500250;
                4'd3: coefficient = 64'sd821806413;
                4'd4: coefficient = 64'sd0;
                4'd5: coefficient = -64'sd821806413;
                4'd6: coefficient = -64'sd1518500250;
                4'd7: coefficient = -64'sd1984016189;
                4'd8: coefficient = -64'sd2147483648;
                4'd9: coefficient = -64'sd1984016189;
                4'd10: coefficient = -64'sd1518500250;
                4'd11: coefficient = -64'sd821806413;
                4'd12: coefficient = 64'sd0;
                4'd13: coefficient = 64'sd821806413;
                4'd14: coefficient = 64'sd1518500250;
                4'd15: coefficient = 64'sd1984016189;
            endcase
            magnitude = coefficient < 0 ? -coefficient : coefficient;
            rounded_value = (magnitude + (64'sd1 << (31-OUT_W))) >> (32-OUT_W);
            if (coefficient < 0)
                rounded_value = -rounded_value;
            limit_value = 64'sd1 << (OUT_W-1);
            if (rounded_value >= limit_value)
                rounded_value = limit_value - 1;
            if (rounded_value < -limit_value)
                rounded_value = -limit_value;
            sample = OUT_W'(rounded_value);
        end
    endfunction

    always_comb begin
        out_i = 0;
        out_q = 0;
        if (busy) begin
            out_i = sample(index);
            out_q = sample(index - 4'd4);
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            beat <= 0;
            phase_acc <= 0;
            increment <= 0;
        end else begin
            // The old symbol's last sample advances before the next symbol starts.
            if (transfer)
                phase_acc <= phase_acc + increment;
            if (in_valid && in_ready) begin
                busy <= 1;
                beat <= 0;
                increment <= phase_step(in_symbol);
            end else if (transfer) begin
                if (last_sample) begin
                    busy <= 0;
                    beat <= 0;
                end else
                    beat <= beat + 1'b1;
            end
        end
    end
endmodule
