module zamba2_ssm_decode (
    input logic clk, rst,
    input logic in_valid,
    output logic in_ready,
    input logic [63:0] in_x,
    input logic [31:0] in_decay,
    input logic [95:0] in_dB, in_C,
    input logic [31:0] in_D,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y,
    output logic [191:0] out_state
);
    logic signed [15:0] state [0:11];
    logic signed [15:0] next_state [0:11];
    logic [63:0] next_y;
    logic [191:0] packed_state;
    logic signed [15:0] x, decay, skip, b, c, total, value;
    integer h, d, n, index;
    function automatic signed [15:0] qmul(input signed [15:0] a, b);
        reg signed [31:0] product;
        begin product = a * b; qmul = product >>> 8; end
    endfunction
    always @* begin
        next_y = 0;
        packed_state = 0;
        x=0; decay=0; skip=0; b=0; c=0; total=0; value=0; index=0;
        for (h=0; h<2; h=h+1) begin
            decay = in_decay[16*h +: 16];
            skip = in_D[16*h +: 16];
            for (d=0; d<2; d=d+1) begin
                x = in_x[16*(2*h+d) +: 16];
                total = 0;
                for (n=0; n<3; n=n+1) begin
                    index = (2*h+d)*3+n;
                    b = in_dB[16*(3*h+n) +: 16];
                    c = in_C[16*(3*h+n) +: 16];
                    value = qmul(state[index], decay) + qmul(b, x);
                    next_state[index] = value;
                    total = total + qmul(value, c);
                    packed_state[16*index +: 16] = value;
                end
                next_y[16*(2*h+d) +: 16] = total + qmul(skip, x);
            end
        end
    end
    assign in_ready = !rst && (!out_valid || out_ready);
    integer k;
    always @(posedge clk) begin
        if (rst) begin
            for (k=0; k<12; k=k+1) state[k] <= 0;
            out_y <= 0;
            out_state <= 0;
            out_valid <= 0;
        end else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin
                for (k=0; k<12; k=k+1) state[k] <= next_state[k];
                out_y <= next_y;
                out_state <= packed_state;
            end
        end
    end
endmodule
