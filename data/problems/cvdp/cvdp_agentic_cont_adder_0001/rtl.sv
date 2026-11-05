module continuous_adder #(
    parameter int DATA_WIDTH = 32, parameter int ENABLE_THRESHOLD = 0,
    parameter int THRESHOLD = 16, parameter int REGISTER_OUTPUT = 0
) (
    input logic clk, rst_n, valid_in,
    input logic [DATA_WIDTH-1:0] data_in,
    input logic accumulate_enable, flush,
    output logic [DATA_WIDTH-1:0] sum_out,
    output logic sum_valid
);
    logic [DATA_WIDTH-1:0] sum_reg,qualified_sum;
    logic qualified;
    assign qualified=!ENABLE_THRESHOLD || $unsigned(sum_reg)>=THRESHOLD;
    assign qualified_sum=qualified ? sum_reg : DATA_WIDTH'(0);
    always_ff @(posedge clk or negedge rst_n)
        if(!rst_n) sum_reg<='0;
        else if(flush) sum_reg<='0;
        else if(valid_in && accumulate_enable) sum_reg<=sum_reg+data_in;
    if(REGISTER_OUTPUT) begin: registered_output
        always_ff @(posedge clk or negedge rst_n)
            if(!rst_n) begin sum_out<='0; sum_valid<=0; end
            else begin sum_out<=qualified_sum; sum_valid<=qualified; end
    end else begin: combinational_output
        assign sum_out=rst_n ? qualified_sum : DATA_WIDTH'(0);
        assign sum_valid=rst_n && qualified;
    end
endmodule
