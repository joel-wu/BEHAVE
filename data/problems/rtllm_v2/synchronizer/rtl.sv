module synchronizer(
    input logic clk_a_level, clk_b_level, arstn, brstn,
    input logic [3:0] data_in, input logic data_en,
    output logic [3:0] dataout
);
    logic [3:0] source_data=0;
    logic source_enable=0, enable_first=0, enable_second=0;
    initial dataout=0;
    always_ff @(posedge clk_a_level or negedge arstn) begin
        if(!arstn) begin source_data<=0; source_enable<=0; end
        else begin source_data<=data_in; source_enable<=data_en; end
    end
    always_ff @(posedge clk_b_level or negedge brstn) begin
        if(!brstn) begin enable_first<=0; enable_second<=0; dataout<=0; end
        else begin
            enable_first<=source_enable;
            enable_second<=enable_first;
            if(enable_second) dataout<=source_data;
        end
    end
endmodule
