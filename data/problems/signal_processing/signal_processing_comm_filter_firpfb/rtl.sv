module firpfb_4phase_4tap #(
    parameter logic [255:0] H_FLAT = 256'h0003000200010001ffff0002fffe00030002ffff0001fffe0004000300020001,
    parameter logic [15:0] SCALE = 16'd1
) (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [15:0] in_x,
    input logic [1:0] in_i,
    output logic out_valid,
    input logic out_ready,
    output logic [63:0] out_y
);
    logic pending;
    logic signed [15:0] delay0, delay1, delay2;
    logic signed [63:0] total, result_value;
    function automatic logic signed [63:0] coefficient(input integer index);
        coefficient = 64'($signed(H_FLAT[16*index +: 16]));
    endfunction
    assign in_ready = !pending || out_ready;
    assign out_valid = pending && !rst;
    always_comb begin
        total = coefficient(int'(in_i))*64'($signed(in_x))
              + coefficient(int'(in_i)+4)*64'(delay0)
              + coefficient(int'(in_i)+8)*64'(delay1)
              + coefficient(int'(in_i)+12)*64'(delay2);
        result_value = total * 64'($signed(SCALE));
    end
    always_ff @(posedge clk) begin
        if (rst) begin
            pending <= 0;
            out_y <= 0;
            delay0 <= 0; delay1 <= 0; delay2 <= 0;
        end else if (in_ready) begin
            pending <= in_valid;
            if (in_valid) begin
                out_y <= result_value;
                delay2 <= delay1; delay1 <= delay0; delay0 <= $signed(in_x);
            end
        end
    end
endmodule
