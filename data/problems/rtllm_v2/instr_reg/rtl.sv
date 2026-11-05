module instr_reg (
    input logic clk,
    input logic rst,
    input logic [1:0] fetch,
    input logic [7:0] data,
    output logic [2:0] ins,
    output logic [4:0] ad1,
    output logic [7:0] ad2
);
    logic [7:0] p1, p2;
    always_ff @(posedge clk or negedge rst) begin
        if (!rst) begin
            p1 <= 0;
            p2 <= 0;
        end else begin
            if (fetch == 1) p1 <= data;
            else if (fetch == 2) p2 <= data;
        end
    end
    assign ins = p1[7:5];
    assign ad1 = p1[4:0];
    assign ad2 = p2;
endmodule
