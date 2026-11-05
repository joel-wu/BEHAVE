module signed_comparator #(
    parameter int DATA_WIDTH = 16, parameter int REGISTER_OUTPUT = 0,
    parameter int ENABLE_TOLERANCE = 0, parameter int TOLERANCE = 0,
    parameter int SHIFT_LEFT = 0
) (
    input logic clk, rst_n, enable, bypass,
    input logic signed [DATA_WIDTH-1:0] a, b,
    output logic gt, lt, eq
);
    logic signed [DATA_WIDTH-1:0] a_shifted,b_shifted;
    logic signed [DATA_WIDTH:0] difference;
    logic [DATA_WIDTH:0] magnitude;
    logic [2:0] relation;
    assign a_shifted=a <<< SHIFT_LEFT;
    assign b_shifted=b <<< SHIFT_LEFT;
    assign difference=$signed({a_shifted[DATA_WIDTH-1],a_shifted})-
                      $signed({b_shifted[DATA_WIDTH-1],b_shifted});
    assign magnitude=difference<0 ? $unsigned(-difference) : $unsigned(difference);
    always_comb begin
        relation=3'b000;
        if(bypass) relation=3'b001;
        else if(enable) begin
            if(ENABLE_TOLERANCE && magnitude<=TOLERANCE) relation=3'b001;
            else if(a_shifted>b_shifted) relation=3'b100;
            else if(a_shifted<b_shifted) relation=3'b010;
            else relation=3'b001;
        end
    end
    if(REGISTER_OUTPUT) begin: registered_output
        always_ff @(posedge clk or negedge rst_n)
            if(!rst_n) {gt,lt,eq}<=3'b000;
            else {gt,lt,eq}<=relation;
    end else begin: combinational_output
        assign {gt,lt,eq}=relation;
    end
endmodule
