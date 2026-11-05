module fibonacci_series (
    input logic clk, rst,
    output logic [31:0] fib_out,
    output logic overflow_flag
);
    logic [31:0] reg_a, reg_b;
    logic pending;
    wire [32:0] total={1'b0,reg_a}+{1'b0,reg_b};
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            reg_a<=0; reg_b<=1; fib_out<=0; pending<=0; overflow_flag<=0;
        end else if (pending) begin
            reg_a<=0; reg_b<=1; fib_out<=0; pending<=0; overflow_flag<=1;
        end else begin
            fib_out<=reg_b; overflow_flag<=0;
            if (total[32]) pending<=1;
            else begin reg_a<=reg_b; reg_b<=total[31:0]; end
        end
    end
endmodule
