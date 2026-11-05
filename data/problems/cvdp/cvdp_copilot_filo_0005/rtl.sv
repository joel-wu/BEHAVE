module FILO_RTL #(parameter int DATA_WIDTH = 8, parameter int FILO_DEPTH = 16) (
    input logic clk, reset, push, pop,
    input logic [DATA_WIDTH-1:0] data_in,
    output logic [DATA_WIDTH-1:0] data_out,
    output logic full, empty
);
    localparam int COUNT_WIDTH=(FILO_DEPTH<2) ? 1 : $clog2(FILO_DEPTH+1);
    logic [COUNT_WIDTH-1:0] count;
    logic [DATA_WIDTH-1:0] storage [0:FILO_DEPTH-1];
    assign full=(count==FILO_DEPTH);
    assign empty=(count==0);
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            count<=0; data_out<=0;
            for (int i=0; i<FILO_DEPTH; i++) storage[i]<=0;
        end else if (push && pop && empty) begin
            data_out<=data_in;
        end else if (pop && !empty) begin
            data_out<=storage[count-1'b1];
            if (push && !full) storage[count-1'b1]<=data_in;
            else count<=count-1'b1;
        end else if (push && !full) begin
            storage[count]<=data_in; count<=count+1'b1;
        end
    end
endmodule
