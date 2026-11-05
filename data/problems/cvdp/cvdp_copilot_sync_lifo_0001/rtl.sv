module sync_lifo #(parameter int DATA_WIDTH = 8, parameter int ADDR_WIDTH = 3) (
    input logic clock, reset, write_en, read_en,
    input logic [DATA_WIDTH-1:0] data_in,
    output logic empty, full,
    output logic [DATA_WIDTH-1:0] data_out
);
    localparam int DEPTH=1<<ADDR_WIDTH;
    logic [DATA_WIDTH-1:0] memory [0:DEPTH-1];
    logic [ADDR_WIDTH:0] count;
    wire pop=read_en && !empty;
    wire push=write_en && !full;
    assign empty=count==0;
    assign full=count==DEPTH;
    always_ff @(posedge clock) begin
        if (reset) begin
            count<=0; data_out<=0;
            for (int i=0; i<DEPTH; i++) memory[i]<=0;
        end else begin
            if (pop) data_out<=memory[count-1'b1];
            if (push) memory[pop ? count-1'b1 : count]<=data_in;
            case ({push,pop})
                2'b10: count<=count+1'b1;
                2'b01: count<=count-1'b1;
                default: ;
            endcase
        end
    end
endmodule
