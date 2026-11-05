module TopModule (input logic clk, init_reset, load, input logic [255:0] data, output logic [255:0] q);
    logic [255:0] next_q;
    always_comb begin
        for(int r=0;r<16;r++) begin
            for(int c=0;c<16;c++) begin
                next_q[16*r+c]=0;
                case (4'(q[16*((r+15)%16)+(c+15)%16])+4'(q[16*((r+15)%16)+c])+
                      4'(q[16*((r+15)%16)+(c+1)%16])+4'(q[16*r+(c+15)%16])+
                      4'(q[16*r+(c+1)%16])+4'(q[16*((r+1)%16)+(c+15)%16])+
                      4'(q[16*((r+1)%16)+c])+4'(q[16*((r+1)%16)+(c+1)%16]))
                    2: next_q[16*r+c]=q[16*r+c];
                    3: next_q[16*r+c]=1;
                    default: next_q[16*r+c]=0;
                endcase
            end
        end
    end
    always_ff @(posedge clk) if(init_reset) q<=0; else if(load) q<=data; else q<=next_q;
endmodule
