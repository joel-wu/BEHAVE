module multiplexer #(
    parameter int DATA_WIDTH = 8, parameter int NUM_INPUTS = 4,
    parameter int REGISTER_OUTPUT = 0, parameter int HAS_DEFAULT = 0,
    parameter logic [DATA_WIDTH-1:0] DEFAULT_VALUE = 0
) (
    input logic clk, rst_n,
    input logic [DATA_WIDTH*NUM_INPUTS-1:0] inp,
    input logic [((NUM_INPUTS>1) ? $clog2(NUM_INPUTS) : 1)-1:0] sel,
    input logic bypass,
    output logic [DATA_WIDTH-1:0] out
);
    wire [DATA_WIDTH-1:0] words [0:NUM_INPUTS-1];
    for(genvar i=0;i<NUM_INPUTS;i++) begin: unpack_inputs
        assign words[i]=inp[i*DATA_WIDTH+:DATA_WIDTH];
    end
    logic [DATA_WIDTH-1:0] selected;
    always_comb begin
        if(bypass) selected=words[0];
        else if(sel<NUM_INPUTS) selected=words[sel];
        else selected=HAS_DEFAULT ? DEFAULT_VALUE : DATA_WIDTH'(0);
    end
    if(REGISTER_OUTPUT) begin: registered_output
        always_ff @(posedge clk or negedge rst_n)
            if(!rst_n) out<='0;
            else out<=selected;
    end else begin: combinational_output
        assign out=selected;
    end
endmodule
