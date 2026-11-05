module RAM #(parameter int WIDTH = 6, parameter int DEPTH = 8)(
    input logic clk, rst_n,
    input logic write_en,
    input logic [7:0] write_addr,
    input logic [WIDTH-1:0] write_data,
    input logic read_en,
    input logic [7:0] read_addr,
    output logic [WIDTH-1:0] read_data
);
    logic [WIDTH-1:0] memory [0:DEPTH-1];
    always_ff @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin
            read_data<='0;
            for(int i=0;i<DEPTH;i++) memory[i]<='0;
        end else begin
            if(write_en && write_addr<DEPTH) memory[write_addr]<=write_data;
            if(read_en && read_addr<DEPTH) read_data<=memory[read_addr];
            else read_data<='0;
        end
    end
endmodule
