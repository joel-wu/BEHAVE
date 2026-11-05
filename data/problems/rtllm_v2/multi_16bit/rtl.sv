module multi_16bit (
    input logic clk,
    input logic rst_n,
    input logic start,
    input logic [15:0] ain,
    input logic [15:0] bin,
    output logic [31:0] yout,
    output logic done
);
    logic [15:0] areg, breg;
    logic [4:0] iteration;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            areg <= 16'd0;
            breg <= 16'd0;
            yout <= 32'd0;
            iteration <= 5'd0;
            done <= 1'b0;
        end else begin
            done <= 1'b0;
            if (!start) begin
                iteration <= 5'd0;
            end else if (iteration == 5'd0) begin
                areg <= ain;
                breg <= bin;
                yout <= 32'd0;
                iteration <= 5'd1;
            end else if (iteration < 5'd17) begin
                if (areg[iteration-1]) yout <= yout + ({16'd0,breg} << (iteration-1));
                iteration <= iteration + 5'd1;
                if (iteration == 5'd16) done <= 1'b1;
            end
        end
    end
endmodule
