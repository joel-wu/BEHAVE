module fsm (
    input logic IN,
    input logic CLK,
    input logic RST,
    output logic MATCH
);
    logic [3:0] history;

    always_ff @(posedge CLK or posedge RST) begin
        if (RST) history <= 4'b0000;
        else history <= {history[2:0], IN};
    end

    assign MATCH = !RST && history == 4'b1001 && IN;
endmodule
