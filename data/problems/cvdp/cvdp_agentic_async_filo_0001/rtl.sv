module async_filo #(
    parameter int DATA_WIDTH=16,
    parameter int DEPTH=8
)(
    input logic w_clk_level, r_clk_level, w_reset_level, r_reset_level, push, pop,
    input logic [DATA_WIDTH-1:0] w_data,
    output logic [DATA_WIDTH-1:0] r_data,
    output wire r_empty, w_full,
    output logic r_busy, r_valid
);
    localparam int CW=$clog2(DEPTH+1);
    wire reset_all=w_reset_level | r_reset_level;
    logic [DATA_WIDTH-1:0] mem[0:DEPTH-1];
    logic [CW-1:0] count=0;
    wire [CW-1:0] gray=count^(count>>1);
    (* ASYNC_REG="TRUE" *) logic [CW-1:0] gray1=0, gray2=0;
    (* ASYNC_REG="TRUE" *) logic req1=0, req2=0, done1=0, done2=0;
    logic request=0, completed=0, success=0;
    logic [DATA_WIDTH-1:0] reply=0;
    initial begin
        r_data=0; r_busy=0; r_valid=0;
        for(integer i=0;i<DEPTH;i=i+1) mem[i]=0;
    end
    assign w_full=count==DEPTH;
    assign r_empty=gray2==0;
    always_ff @(posedge w_clk_level or posedge reset_all) begin : owner
        integer n;
        if(reset_all) begin
            count<=0; req1<=0; req2<=0; completed<=0; reply<=0; success<=0;
        end else begin
            req1<=request; req2<=req1;
            n=count;
            if(req2!=completed) begin
                success<=count!=0; reply<=0;
                if(count!=0) begin n=n-1; reply<=mem[n]; end
                completed<=req2;
            end
            if(push && !w_full) begin
                mem[n]<=w_data; n=n+1;
            end
            count<=CW'(n);
        end
    end
    always_ff @(posedge r_clk_level or posedge reset_all) begin
        if(reset_all) begin
            request<=0; done1<=0; done2<=0; gray1<=0; gray2<=0;
            r_data<=0; r_busy<=0; r_valid<=0;
        end else begin
            done1<=completed; done2<=done1; gray1<=gray; gray2<=gray1;
            r_valid<=0;
            if(r_busy) begin
                if(done2==request) begin r_busy<=0; r_valid<=success; r_data<=reply; end
            end else if(pop) begin request<=!request; r_busy<=1; end
        end
    end
endmodule
